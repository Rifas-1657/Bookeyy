"""
app/knowledge/sources/github.py — GitHub repository → project-oriented documents.

Uses the public REST API (unauthenticated: 60 req/h; set GITHUB_TOKEN for 5000/h)
for repo metadata, README and the recursive file tree, then raw.githubusercontent.com
(not rate-limited by the API quota) for the top-N source / notebook / markdown files.

Produces:
  • 1 "project overview" document (description, topics, README, file tree)
  • 1 document per top-level folder group with the selected files' contents
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import OrderedDict
from typing import Any
from urllib.parse import quote, urlparse

from app.knowledge.sources.base import SourceDocument, SourceError, SourcePreview, clip, http_client

log = logging.getLogger(__name__)

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

_CODE_EXT = {
    ".py": "python", ".ipynb": "notebook", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".kt": "kotlin",
    ".swift": "swift", ".scala": "scala", ".r": "r", ".jl": "julia", ".sql": "sql", ".sh": "bash",
    ".html": "html", ".css": "css", ".dart": "dart", ".lua": "lua", ".m": "matlab",
}
_DOC_EXT = {".md": "markdown", ".rst": "rst", ".txt": "text"}
_SKIP_DIRS = {
    "node_modules", "vendor", "dist", "build", ".git", ".github", "__pycache__", ".venv", "venv",
    "env", "site-packages", ".idea", ".vscode", "target", "out", "coverage", ".next", "third_party",
}
_SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "license", "license.md", "license.txt"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def parse_github_url(url: str) -> tuple[str, str, str | None, str | None]:
    """→ (owner, repo, ref|None, subpath|None)."""
    p = urlparse(url.strip())
    if (p.hostname or "").lower() not in ("github.com", "www.github.com"):
        raise SourceError("Not a github.com URL.")
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        raise SourceError("GitHub URL must point to a repository: https://github.com/<owner>/<repo>")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    ref = sub = None
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        ref = parts[3]
        sub = "/".join(parts[4:]) or None
    if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", repo):
        raise SourceError("Invalid GitHub owner/repo.")
    return owner, repo, ref, sub


def _headers(raw: bool = False) -> dict[str, str]:
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.getenv("GITHUB_TOKEN", "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


async def _api_get(client, path: str, *, raw: bool = False, allow_404: bool = False):
    r = await client.get(f"{API}{path}", headers=_headers(raw))
    if r.status_code == 404 and allow_404:
        return None
    if r.status_code == 404:
        raise SourceError("GitHub repository not found (or private).")
    if r.status_code in (403, 429):
        reset = r.headers.get("x-ratelimit-reset", "")
        raise SourceError(
            "GitHub API rate limit reached"
            + (f" (resets at unix {reset})" if reset else "")
            + ". Set GITHUB_TOKEN to raise the limit."
        )
    if r.status_code != 200:
        raise SourceError(f"GitHub API error HTTP {r.status_code} for {path}.")
    return r.text if raw else r.json()


async def preview_github(url: str) -> SourcePreview:
    owner, repo, _ref, _sub = parse_github_url(url)
    async with http_client(timeout=20) as client:
        info = await _api_get(client, f"/repos/{owner}/{repo}")
    return SourcePreview(
        type="github",
        url=info.get("html_url") or url,
        title=info.get("full_name") or f"{owner}/{repo}",
        kind="repository",
        description=info.get("description") or "",
        author=(info.get("owner") or {}).get("login", owner),
        size_bytes=int(info.get("size") or 0) * 1024,
        thumbnail=(info.get("owner") or {}).get("avatar_url"),
        extra={
            "stars": info.get("stargazers_count"),
            "forks": info.get("forks_count"),
            "language": info.get("language"),
            "topics": info.get("topics") or [],
            "defaultBranch": info.get("default_branch"),
            "license": (info.get("license") or {}).get("spdx_id"),
            "updatedAt": info.get("pushed_at"),
        },
    )


def _natural_key(path: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path)]


def select_files(tree: list[dict], *, max_files: int, max_file_bytes: int, subpath: str | None = None) -> list[dict]:
    """Pick the most useful blobs: shallow first, docs/notebooks/code, natural order."""
    picked: list[tuple[tuple, dict]] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        if subpath and not path.startswith(subpath.rstrip("/") + "/"):
            continue
        segs = path.split("/")
        if any(s in _SKIP_DIRS or s.startswith(".") for s in segs[:-1]):
            continue
        name = segs[-1].lower()
        if name in _SKIP_FILES or name.startswith("readme"):
            continue
        ext = os.path.splitext(name)[1]
        if ext not in _CODE_EXT and ext not in _DOC_EXT:
            continue
        size = int(node.get("size") or 0)
        if size == 0 or size > max_file_bytes:
            continue
        # docs + notebooks are high-signal for teaching; then code
        prio = 0 if ext in (".md", ".ipynb") else 1 if ext in _CODE_EXT else 2
        picked.append(((len(segs), prio, _natural_key(path)), node))
    picked.sort(key=lambda x: x[0])
    # Round-robin across top-level folders so every "chapter" folder gets coverage.
    buckets: "OrderedDict[str, list[dict]]" = OrderedDict()
    for _, n in sorted(picked, key=lambda x: _natural_key(x[1]["path"].split("/")[0])):
        buckets.setdefault(n["path"].split("/")[0] if "/" in n["path"] else "", []).append(n)
    for b in buckets.values():
        b.sort(key=lambda n: (n["path"].count("/"),
                              0 if os.path.splitext(n["path"])[1].lower() in (".md", ".ipynb") else 1,
                              _natural_key(n["path"])))
    chosen: list[dict] = []
    while len(chosen) < max_files and any(buckets.values()):
        for b in buckets.values():
            if b and len(chosen) < max_files:
                chosen.append(b.pop(0))
    chosen.sort(key=lambda n: _natural_key(n["path"]))
    return chosen


def notebook_to_markdown(raw: str, max_output_chars: int = 600) -> str:
    try:
        nb = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    lang = ((nb.get("metadata") or {}).get("kernelspec") or {}).get("language", "python")
    out: list[str] = []
    for cell in nb.get("cells") or []:
        src = cell.get("source") or ""
        src = "".join(src) if isinstance(src, list) else str(src)
        if not src.strip():
            continue
        if cell.get("cell_type") == "markdown":
            out.append(src.strip())
        elif cell.get("cell_type") == "code":
            out.append(f"```{lang}\n{src.rstrip()}\n```")
            texts = []
            for o in cell.get("outputs") or []:
                t = o.get("text") or (o.get("data") or {}).get("text/plain")
                if t:
                    texts.append("".join(t) if isinstance(t, list) else str(t))
            if texts:
                out.append("Output:\n```\n" + clip("".join(texts).strip(), max_output_chars) + "\n```")
    return "\n\n".join(out)


def _tree_listing(tree: list[dict], limit: int = 300) -> str:
    kept = [n for n in tree if not any(s in _SKIP_DIRS for s in n.get("path", "").split("/"))]
    kept.sort(key=lambda n: _natural_key(n.get("path", "")))
    lines = [f"- {n['path']}{'/' if n.get('type') == 'tree' else ''}" for n in kept[:limit]]
    if len(kept) > limit:
        lines.append(f"- … and {len(kept) - limit} more")
    return "\n".join(lines)


async def fetch_github(url: str) -> list[SourceDocument]:
    owner, repo, ref, sub = parse_github_url(url)
    max_files = _env_int("GITHUB_MAX_FILES", 40)
    max_file_bytes = _env_int("GITHUB_MAX_FILE_BYTES", 80_000)
    max_total_chars = _env_int("GITHUB_MAX_TOTAL_CHARS", 500_000)

    async with http_client(timeout=30) as client:
        info = await _api_get(client, f"/repos/{owner}/{repo}")
        branch = ref or info.get("default_branch") or "main"
        readme = await _api_get(client, f"/repos/{owner}/{repo}/readme?ref={quote(branch)}", raw=True, allow_404=True)
        tree_resp = await _api_get(client, f"/repos/{owner}/{repo}/git/trees/{quote(branch)}?recursive=1")
        tree = tree_resp.get("tree") or []
        truncated = bool(tree_resp.get("truncated"))
        files = select_files(tree, max_files=max_files, max_file_bytes=max_file_bytes, subpath=sub)

        sem = asyncio.Semaphore(6)

        async def _raw(path: str) -> str | None:
            async with sem:
                r = await client.get(f"{RAW}/{owner}/{repo}/{quote(branch)}/{quote(path)}")
                if r.status_code != 200:
                    return None
                return r.text

        contents = await asyncio.gather(*[_raw(f["path"]) for f in files])

    full_name = info.get("full_name") or f"{owner}/{repo}"
    html_url = info.get("html_url") or f"https://github.com/{owner}/{repo}"
    topics = info.get("topics") or []
    meta_common = {
        "repo": full_name, "branch": branch, "stars": info.get("stargazers_count"),
        "language": info.get("language"), "topics": topics,
        "license": (info.get("license") or {}).get("spdx_id"),
    }

    overview = [f"# {full_name} — project overview", "", f"Repository: {html_url}"]
    if info.get("description"):
        overview += ["", f"Description: {info['description']}"]
    if topics:
        overview.append(f"Topics: {', '.join(topics)}")
    if info.get("language"):
        overview.append(f"Primary language: {info['language']}")
    if readme:
        overview += ["", "## README", "", clip(readme, 60_000)]
    overview += ["", "## File tree", "", _tree_listing(tree)]
    if truncated:
        overview.append("\n(File tree truncated by GitHub API.)")

    docs = [SourceDocument(
        title=f"{full_name} — overview",
        url=html_url,
        kind="github",
        text="\n".join(overview) + "\n",
        metadata={**meta_common, "part": "overview", "fileCount": len(tree), "selectedFiles": [f["path"] for f in files]},
    )]

    # Group files by top-level folder (chapter-like structure in course repos)
    groups: "OrderedDict[str, list[tuple[str, str]]]" = OrderedDict()
    total = 0
    for f, body in zip(files, contents):
        if body is None:
            continue
        path = f["path"]
        ext = os.path.splitext(path)[1].lower()
        if ext == ".ipynb":
            body = notebook_to_markdown(body)
        elif ext not in _DOC_EXT:
            body = f"```{_CODE_EXT.get(ext, '')}\n{body.rstrip()}\n```"
        if total + len(body) > max_total_chars:
            break
        total += len(body)
        top = path.split("/")[0] if "/" in path else "(root)"
        groups.setdefault(top, []).append((path, body))

    for top, items in groups.items():
        lines = [f"# {full_name} / {top}", "", f"Files from {html_url}/tree/{branch}/{'' if top == '(root)' else top}", ""]
        for path, body in items:
            lines += [f"## {path}", "", body, ""]
        docs.append(SourceDocument(
            title=f"{full_name} — {top}",
            url=f"{html_url}/tree/{branch}/{'' if top == '(root)' else top}".rstrip("/"),
            kind="github",
            text="\n".join(lines),
            metadata={**meta_common, "part": "files", "folder": top, "files": [p for p, _ in items]},
        ))
    return docs

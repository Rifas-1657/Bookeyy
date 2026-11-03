"""
app/knowledge/sources/reddit.py — Reddit thread → post + top comments document.

Uses the public `<thread-url>.json` endpoint with a descriptive User-Agent
(REDDIT_USER_AGENT env overrides).  Falls back to old.reddit.com if www is blocked.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.knowledge.sources.base import DEFAULT_UA, SourceDocument, SourceError, SourcePreview, clip, http_client

_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "m.reddit.com"}


def _ua() -> str:
    return os.getenv("REDDIT_USER_AGENT", DEFAULT_UA)


def normalize_reddit_url(url: str) -> str:
    """→ canonical 'https://www.reddit.com/r/<sub>/comments/<id>/<slug>' (no trailing slash/query)."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if host == "redd.it":
        pid = p.path.strip("/").split("/")[0]
        if not pid:
            raise SourceError("Invalid redd.it link.")
        return f"https://www.reddit.com/comments/{pid}"
    if host not in _HOSTS:
        raise SourceError("Not a reddit.com URL.")
    path = re.sub(r"/+$", "", p.path)
    if path.endswith(".json"):
        path = path[:-5]
    if "/comments/" not in path:
        raise SourceError("Reddit URL must point to a thread (…/comments/<id>/…), not a subreddit or user.")
    return f"https://www.reddit.com{path}"


async def _get_json(canonical: str) -> Any:
    params = {"limit": "100", "depth": "3", "sort": "top", "raw_json": "1"}
    last = ""
    async with http_client(timeout=20, headers={"User-Agent": _ua(), "Accept": "application/json"}) as client:
        for base in (canonical, canonical.replace("https://www.reddit.com", "https://old.reddit.com")):
            r = await client.get(base + ".json", params=params)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    last = "non-JSON response"
                    continue
            if r.status_code == 404:
                raise SourceError("Reddit thread not found (deleted or private).")
            last = f"HTTP {r.status_code}"
    raise SourceError(f"Reddit refused the request ({last}). Try again later or set REDDIT_USER_AGENT.")


def _post_of(data: Any) -> dict:
    try:
        return data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        raise SourceError("Unexpected Reddit response shape.")


async def preview_reddit(url: str) -> SourcePreview:
    canonical = normalize_reddit_url(url)
    post = _post_of(await _get_json(canonical))
    thumb = post.get("thumbnail")
    return SourcePreview(
        type="reddit",
        url=f"https://www.reddit.com{post.get('permalink', '')}".rstrip("/") if post.get("permalink") else canonical,
        title=post.get("title") or "Reddit thread",
        kind="thread",
        description=clip(post.get("selftext") or "", 400),
        author=f"u/{post.get('author')}" if post.get("author") else "",
        thumbnail=thumb if isinstance(thumb, str) and thumb.startswith("http") else None,
        extra={
            "subreddit": post.get("subreddit_name_prefixed"),
            "score": post.get("score"),
            "numComments": post.get("num_comments"),
            "createdUtc": post.get("created_utc"),
        },
    )


def _walk(children: list, depth: int, max_depth: int, out: list[dict]) -> None:
    for c in children or []:
        if not isinstance(c, dict) or c.get("kind") != "t1":
            continue
        d = c.get("data") or {}
        body = (d.get("body") or "").strip()
        author = d.get("author") or ""
        if body and body not in ("[deleted]", "[removed]") and author != "AutoModerator":
            out.append({"author": author, "score": d.get("score") or 0, "body": body, "depth": depth})
        replies = d.get("replies")
        if depth < max_depth and isinstance(replies, dict):
            _walk(((replies.get("data") or {}).get("children")) or [], depth + 1, max_depth, out)


def thread_json_to_document(data: Any, canonical: str, max_comments: int = 40) -> SourceDocument:
    post = _post_of(data)
    comments: list[dict] = []
    try:
        _walk(data[1]["data"]["children"], 0, 2, comments)
    except (KeyError, IndexError, TypeError):
        pass
    top_level = sorted([c for c in comments if c["depth"] == 0], key=lambda c: -c["score"])
    replies = sorted([c for c in comments if c["depth"] > 0], key=lambda c: -c["score"])
    chosen = (top_level + replies)[:max_comments]

    created = post.get("created_utc")
    when = datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat() if created else ""
    title = post.get("title") or "Reddit thread"
    lines = [
        f"# {title}", "",
        f"Source: Reddit {post.get('subreddit_name_prefixed', '')} thread {canonical}",
        f"Posted by u/{post.get('author', '?')} {when} · score {post.get('score', 0)} · {post.get('num_comments', 0)} comments",
    ]
    if post.get("selftext"):
        lines += ["", "## Post", "", post["selftext"].strip()]
    elif post.get("url") and post.get("url") != canonical:
        lines += ["", f"Linked URL: {post['url']}"]
    if chosen:
        lines += ["", "## Top comments", ""]
        for c in chosen:
            prefix = "  " * c["depth"]
            lines.append(f"{prefix}- **u/{c['author']}** (score {c['score']}): {clip(c['body'], 2500)}")
    return SourceDocument(
        title=title,
        url=canonical,
        kind="reddit",
        text="\n".join(lines) + "\n",
        metadata={
            "subreddit": post.get("subreddit"),
            "author": post.get("author"),
            "score": post.get("score"),
            "numComments": post.get("num_comments"),
            "commentsIncluded": len(chosen),
        },
    )


async def fetch_reddit(url: str) -> list[SourceDocument]:
    canonical = normalize_reddit_url(url)
    data = await _get_json(canonical)
    return [thread_json_to_document(data, canonical)]

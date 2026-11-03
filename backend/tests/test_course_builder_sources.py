"""Course builder — source adapter unit tests (HTTP mocked with respx, subprocess mocked)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx

from app.knowledge.sources import SourceError, detect_source_type, validate_source
from app.knowledge.sources import github as gh
from app.knowledge.sources import papers, reddit, youtube

# ── detection / validation ───────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "youtube"),
    ("https://youtu.be/jNQXAC9IVRw", "youtube"),
    ("https://github.com/CodeWithHarry/The-Ultimate-Python-Course", "github"),
    ("https://www.reddit.com/r/learnpython/comments/abc123/some_title/", "reddit"),
    ("https://arxiv.org/abs/1706.03762", "arxiv"),
    ("1706.03762", "arxiv"),
    ("https://example.com/paper.pdf", "pdf_url"),
    ("https://docs.python.org/3/tutorial/", "web"),
])
def test_detect_source_type(url, expected):
    assert detect_source_type(url) == expected


def test_youtube_normalize_and_playlist_rejection():
    assert youtube.normalize_youtube_url("https://youtu.be/jNQXAC9IVRw?t=3")[1] == "jNQXAC9IVRw"
    assert youtube.normalize_youtube_url("https://www.youtube.com/shorts/jNQXAC9IVRw")[0] == \
        "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    # watch URL inside a playlist → list stripped
    assert youtube.normalize_youtube_url(
        "https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PL123")[0].endswith("v=jNQXAC9IVRw")
    with pytest.raises(SourceError, match="playlist"):
        youtube.normalize_youtube_url("https://www.youtube.com/playlist?list=PL123")
    with pytest.raises(SourceError):
        validate_source("youtube", "https://example.com/watch?v=x")


def test_validate_rejects_private_ip():
    with pytest.raises(SourceError):
        validate_source("web", "http://127.0.0.1/admin")


# ── YouTube ───────────────────────────────────────────────────────────────────

ANALYZER_JSON = {
    "warnings": [],
    "metadata": {"platform": "ytdlp", "title": "Me at the zoo", "duration": 19, "uploader": "jawed",
                 "thumbnailUrl": "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg", "description": "zoo"},
    "transcript": [
        {"time": "0:01", "text": "All right, so here we are, in front of the elephants", "endTime": "0:03"},
        {"time": "0:05", "text": "the cool thing about these guys is that they have really...", "endTime": "0:07"},
    ],
    "chapters": [{"time": "0:00", "title": "Intro"}, {"time": "0:05", "title": "The cool thing"}],
    "timeline": [],
    "frameCount": 1,
}


async def test_youtube_via_analyzer(monkeypatch):
    calls = []

    async def fake_run(args, timeout, cwd=None):
        calls.append(args)
        return 0, ("progress noise\n" + json.dumps(ANALYZER_JSON)).encode(), b"[100%] done"

    monkeypatch.setattr(youtube, "run_cmd", fake_run)
    monkeypatch.setattr(youtube, "_analyzer_cmd", lambda: ["npx", "-y", "mcp-video-analyzer@latest"])
    docs = await youtube.fetch_youtube("https://youtu.be/jNQXAC9IVRw")
    assert len(docs) == 1
    d = docs[0]
    assert d.title == "Me at the zoo" and d.kind == "youtube"
    assert "### The cool thing (0:05)" in d.text
    assert "[0:01] All right" in d.text
    assert d.metadata["method"] == "mcp-video-analyzer"
    assert len(d.timestamps) == 2 and d.timestamps[1].start == 5.0
    assert "analyze" in calls[0] and "--max-frames" in calls[0]


async def test_youtube_falls_back_to_ytdlp_captions(monkeypatch):
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello <c>world</c>\n\n"
           "00:00:03.000 --> 00:00:05.000\nhello world\nsecond line\n")

    async def fake_run(args, timeout, cwd=None):
        if "analyze" in args:
            return 1, b"", b"Error: boom"
        out_tpl = args[args.index("-o") + 1]
        tmp = Path(out_tpl).parent
        (tmp / "jNQXAC9IVRw.en.vtt").write_text(vtt, "utf-8")
        (tmp / "jNQXAC9IVRw.info.json").write_text(json.dumps({"title": "Zoo", "duration": 19}), "utf-8")
        return 0, b"", b""

    monkeypatch.setattr(youtube, "run_cmd", fake_run)
    monkeypatch.setattr(youtube, "_analyzer_cmd", lambda: ["npx"])
    docs = await youtube.fetch_youtube("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    d = docs[0]
    assert d.metadata["method"] == "yt-dlp-captions"
    assert d.title == "Zoo"
    assert [s.text for s in d.timestamps] == ["hello world", "second line"]
    assert any("mcp-video-analyzer" in w for w in d.metadata["warnings"])


async def test_youtube_all_methods_fail(monkeypatch):
    async def fake_run(args, timeout, cwd=None):
        return 1, b"", b"nope"

    monkeypatch.setattr(youtube, "run_cmd", fake_run)
    monkeypatch.setattr(youtube, "_analyzer_cmd", lambda: ["npx"])

    async def no_whisper(*a, **k):
        raise SourceError("faster-whisper not available")
    monkeypatch.setattr(youtube, "_via_whisper", no_whisper)
    with pytest.raises(SourceError, match="transcript"):
        await youtube.fetch_youtube("https://youtu.be/jNQXAC9IVRw")


# ── GitHub ────────────────────────────────────────────────────────────────────

REPO = {"full_name": "acme/pycourse", "html_url": "https://github.com/acme/pycourse",
        "description": "Learn Python", "default_branch": "main", "topics": ["python"],
        "language": "Python", "stargazers_count": 5, "size": 12, "owner": {"login": "acme", "avatar_url": "https://a/x.png"}}
TREE = {"truncated": False, "tree": [
    {"path": "Chapter 1", "type": "tree"},
    {"path": "Chapter 1/01_hello.py", "type": "blob", "size": 30},
    {"path": "Chapter 2/02_loops.py", "type": "blob", "size": 40},
    {"path": "Chapter 2/notes.ipynb", "type": "blob", "size": 400},
    {"path": "node_modules/x.js", "type": "blob", "size": 10},
    {"path": "big.py", "type": "blob", "size": 10_000_000},
    {"path": "README.md", "type": "blob", "size": 50},
]}
NB = {"metadata": {"kernelspec": {"language": "python"}}, "cells": [
    {"cell_type": "markdown", "source": ["# Loops\n", "for loops"]},
    {"cell_type": "code", "source": "for i in range(2): print(i)", "outputs": [{"text": ["0\n1\n"]}]}]}


def _mock_github(router):
    router.get("https://api.github.com/repos/acme/pycourse").mock(return_value=httpx.Response(200, json=REPO))
    router.get(url__regex=r"https://api\.github\.com/repos/acme/pycourse/readme.*").mock(
        return_value=httpx.Response(200, text="# PyCourse\nA course."))
    router.get(url__regex=r"https://api\.github\.com/repos/acme/pycourse/git/trees/main.*").mock(
        return_value=httpx.Response(200, json=TREE))
    router.get(url__regex=r"https://raw\.githubusercontent\.com/acme/pycourse/main/Chapter%201/01_hello\.py").mock(
        return_value=httpx.Response(200, text="print('hello')"))
    router.get(url__regex=r"https://raw\.githubusercontent\.com/acme/pycourse/main/Chapter%202/02_loops\.py").mock(
        return_value=httpx.Response(200, text="for i in range(3):\n    print(i)"))
    router.get(url__regex=r"https://raw\.githubusercontent\.com/acme/pycourse/main/Chapter%202/notes\.ipynb").mock(
        return_value=httpx.Response(200, text=json.dumps(NB)))


async def test_github_fetch_builds_project_docs():
    with respx.mock(assert_all_called=False) as router:
        _mock_github(router)
        docs = await gh.fetch_github("https://github.com/acme/pycourse")
    assert docs[0].metadata["part"] == "overview"
    assert "## README" in docs[0].text and "Chapter 2/02_loops.py" in docs[0].text
    assert "node_modules" not in docs[0].text
    folders = [d.metadata.get("folder") for d in docs[1:]]
    assert folders == ["Chapter 1", "Chapter 2"]
    ch2 = docs[2].text
    assert "```python\nfor i in range(3)" in ch2
    assert "# Loops" in ch2 and "Output:" in ch2      # notebook converted
    assert all("big.py" not in d.text.split("## File tree")[0] for d in docs[1:])


async def test_github_preview_and_rate_limit():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/acme/pycourse").mock(return_value=httpx.Response(200, json=REPO))
        p = await gh.preview_github("https://github.com/acme/pycourse/tree/main/Chapter%201")
        assert p.title == "acme/pycourse" and p.kind == "repository" and p.extra["stars"] == 5
        router.get("https://api.github.com/repos/acme/limited").mock(
            return_value=httpx.Response(403, headers={"x-ratelimit-reset": "123"}))
        with pytest.raises(SourceError, match="rate limit"):
            await gh.preview_github("https://github.com/acme/limited")


# ── Reddit ────────────────────────────────────────────────────────────────────

THREAD = [
    {"data": {"children": [{"kind": "t3", "data": {
        "title": "How do I learn Python?", "selftext": "Tips please", "author": "op", "score": 42,
        "subreddit": "learnpython", "subreddit_name_prefixed": "r/learnpython", "num_comments": 3,
        "created_utc": 1700000000, "permalink": "/r/learnpython/comments/abc123/how/"}}]}},
    {"data": {"children": [
        {"kind": "t1", "data": {"author": "a", "score": 10, "body": "Build projects", "replies": {
            "data": {"children": [{"kind": "t1", "data": {"author": "b", "score": 3, "body": "Agreed", "replies": ""}}]}}}},
        {"kind": "t1", "data": {"author": "AutoModerator", "score": 1, "body": "rules", "replies": ""}},
        {"kind": "t1", "data": {"author": "c", "score": 20, "body": "[removed]", "replies": ""}},
        {"kind": "more", "data": {}},
    ]}},
]


async def test_reddit_thread():
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r"https://www\.reddit\.com/r/learnpython/comments/abc123/how\.json.*").mock(
            return_value=httpx.Response(200, json=THREAD))
        docs = await reddit.fetch_reddit("https://www.reddit.com/r/learnpython/comments/abc123/how/?utm=x")
        assert route.called
        assert "User-Agent" in route.calls[0].request.headers
    d = docs[0]
    assert d.title == "How do I learn Python?"
    assert "Build projects" in d.text and "Agreed" in d.text
    assert "AutoModerator" not in d.text and "[removed]" not in d.text
    with pytest.raises(SourceError):
        reddit.normalize_reddit_url("https://www.reddit.com/r/learnpython/")


# ── arXiv ─────────────────────────────────────────────────────────────────────

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <id>http://arxiv.org/abs/1706.03762v7</id>
  <published>2017-06-12T17:57:34Z</published>
  <title>Attention Is All
   You Need</title>
  <summary>The dominant sequence transduction models...</summary>
  <author><name>Ashish Vaswani</name></author>
  <author><name>Noam Shazeer</name></author>
  <link title="pdf" href="http://arxiv.org/pdf/1706.03762v7" rel="related"/>
  <category term="cs.CL"/>
 </entry>
</feed>"""


def test_arxiv_id_parsing():
    assert papers.parse_arxiv_id("https://arxiv.org/pdf/1706.03762v7.pdf") == "1706.03762v7"
    assert papers.parse_arxiv_id("arXiv:1706.03762") == "1706.03762"
    assert papers.parse_arxiv_id("https://arxiv.org/abs/cs/0112017") == "cs/0112017"
    with pytest.raises(SourceError):
        papers.parse_arxiv_id("https://arxiv.org/list/cs.AI/recent")


async def test_arxiv_fetch_with_pdf_text(monkeypatch):
    from app.knowledge.parsing import ParsedPage

    async def fake_pages(url):
        assert url == "https://arxiv.org/pdf/1706.03762v7"
        return [ParsedPage(1, "Self-attention lets every token attend to every other token."),
                ParsedPage(2, "Multi-head attention.")], 1234, url
    monkeypatch.setattr(papers, "_pdf_pages", fake_pages)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(return_value=httpx.Response(200, text=ATOM))
        docs = await papers.fetch_arxiv("https://arxiv.org/abs/1706.03762")
    d = docs[0]
    assert d.title == "Attention Is All You Need"
    assert "## Abstract" in d.text and "## Page 2" in d.text
    assert d.metadata["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert d.metadata["pageCount"] == 2


async def test_arxiv_abstract_only_when_pdf_fails(monkeypatch):
    async def boom(url):
        raise SourceError("down")
    monkeypatch.setattr(papers, "_pdf_pages", boom)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(return_value=httpx.Response(200, text=ATOM))
        docs = await papers.fetch_arxiv("1706.03762")
    assert "abstract only" in docs[0].metadata["warnings"][0]

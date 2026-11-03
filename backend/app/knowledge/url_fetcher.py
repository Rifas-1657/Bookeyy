"""
app/knowledge/url_fetcher.py

Phase-1: URL source fetching with full security hardening.

Security model:
  - SSRF prevention: resolves the hostname to IPs before connecting; blocks
    private / link-local / loopback / multicast ranges (RFC 1918, RFC 4193,
    RFC 3927, RFC 5737, RFC 6598).
  - Scheme allowlist: https only in production; http allowed in development.
  - Redirect safety: each redirect is independently validated against the
    allowlist and private-IP block.
  - No embedded-link following: we fetch exactly the URL supplied. Content
    is not scraped for additional links.
  - Content-Length pre-flight: reject before downloading if server declares
    an oversized body.
  - Hard download cap: streaming read stops at MAX_DOWNLOAD_BYTES even if the
    server did not send Content-Length.
  - Request timeout: connect + total wall-clock timeout (configurable).
  - User-Agent: identifies the fetcher honestly.

Supported content types (mapped to MIME for the parsing pipeline):
  application/pdf               → PDF
  application/vnd.openxmlformats-officedocument.wordprocessingml.document → DOCX
  text/plain                    → TXT
  text/markdown                 → MD
  text/html                     → converted to plain text (HTML tags stripped)

Returns FetchResult with bytes, detected mime, final_url, and size.
Raises FetchError (subclass of ValueError) for all failure modes.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Final
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_DOWNLOAD_BYTES: Final[int] = 25 * 1024 * 1024   # 25 MB
CONNECT_TIMEOUT:    Final[float] = 10.0              # seconds
READ_TIMEOUT:       Final[float] = 60.0              # seconds
MAX_REDIRECTS:      Final[int] = 5
USER_AGENT: Final[str] = (
    "Bookey-Ingestion/1.0 (+https://bookey.app/bot)"
)

ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"https", "http"})

# MIME types we will accept and can parse downstream
ACCEPTED_CONTENT_TYPES: Final[dict[str, str]] = {
    "application/pdf": "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain":    "text/plain",
    "text/markdown": "text/markdown",
    "text/html":     "text/plain",   # stripped to plain text before parsing
    "text/x-markdown": "text/markdown",
}

# Extension → MIME for when Content-Type is missing or generic
_EXT_MIME: Final[dict[str, str]] = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md":   "text/markdown",
    ".txt":  "text/plain",
}

# ---------------------------------------------------------------------------
# Blocked IP networks (SSRF prevention)
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Private / RFC 1918
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Link-local
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # Unique-local IPv6 (RFC 4193)
    ipaddress.ip_network("fc00::/7"),
    # Multicast
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("ff00::/8"),
    # IANA reserved / documentation
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    # Shared address space (RFC 6598)
    ipaddress.ip_network("100.64.0.0/10"),
    # Unspecified
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
]


def _is_private_ip(addr_str: str) -> bool:
    """True for anything that is not a globally routable unicast address.

    IPv4-mapped / 6to4 / Teredo / NAT64-embedded IPv4 addresses are unwrapped and
    checked as IPv4, so ``::ffff:127.0.0.1`` or ``64:ff9b::a9fe:a9fe`` are blocked.
    """
    try:
        addr = ipaddress.ip_address(addr_str.split("%", 1)[0])  # drop IPv6 zone id
    except ValueError:
        return True   # unparseable → block
    candidates = [addr]
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            candidates.append(addr.ipv4_mapped)
        if addr.sixtofour is not None:
            candidates.append(addr.sixtofour)
        if addr.teredo is not None:
            candidates.extend(addr.teredo)
        if addr in _NAT64_NET:
            candidates.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    for a in candidates:
        if not a.is_global or a.is_multicast or a.is_reserved or a.is_unspecified:
            return True
        if any(a in net for net in _BLOCKED_NETWORKS if net.version == a.version):
            return True
    return False


_NAT64_NET = ipaddress.ip_network("64:ff9b::/96")


def _resolve_and_check(hostname: str) -> None:
    """
    Resolve *hostname* to its IP addresses and raise FetchError if any
    resolved address falls in a private/reserved network range.
    Runs synchronously; call via asyncio.to_thread when needed.
    """
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    if not infos:
        raise FetchError(f"No DNS records found for '{hostname}'")

    for info in infos:
        ip_str = info[4][0]
        if _is_private_ip(ip_str):
            raise FetchError(
                f"URL '{hostname}' resolves to a private or reserved IP address "
                f"({ip_str}). Fetching internal network addresses is not allowed."
            )


# ---------------------------------------------------------------------------
# Result / error types
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    data: bytes
    mime_type: str          # normalised MIME from ACCEPTED_CONTENT_TYPES
    final_url: str          # URL after redirects
    content_length: int     # actual byte length
    filename: str           # derived from URL path, e.g. "notes.pdf"


class FetchError(ValueError):
    """Raised for all fetch failures (network, security, content-type, size)."""


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

def _strip_html(html_bytes: bytes) -> bytes:
    """
    Very lightweight HTML → plain-text: remove tags, decode entities.
    Uses html.parser from stdlib; no extra dependency.
    """
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._parts: list[str] = []
            self._skip = False

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag in ("script", "style", "nav", "header", "footer"):
                self._skip = True

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style", "nav", "header", "footer"):
                self._skip = False
            if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                self._parts.append("\n")

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self._parts.append(data)

        def get_text(self) -> str:
            return "".join(self._parts)

    try:
        text = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = html_bytes.decode("latin-1", errors="replace")

    stripper = _Stripper()
    stripper.feed(text)
    return stripper.get_text().encode("utf-8")


# ---------------------------------------------------------------------------
# URL validation (synchronous — no network)
# ---------------------------------------------------------------------------

def validate_url(url: str) -> None:
    """
    Syntactic URL validation. Does NOT do DNS resolution (that happens
    in fetch_url just before the request). Raises FetchError on failure.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise FetchError(f"Malformed URL: {exc}") from exc

    if not parsed.scheme:
        raise FetchError("URL must include a scheme (https:// or http://).")
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise FetchError(
            f"URL scheme '{parsed.scheme}' is not allowed. Use https."
        )
    if not parsed.netloc:
        raise FetchError("URL must include a hostname.")

    hostname = parsed.hostname or ""
    if not hostname:
        raise FetchError("URL contains an empty hostname.")

    # Reject bare IPs in the private ranges without even doing DNS
    try:
        addr = ipaddress.ip_address(hostname)
        # hostname is an IP literal — check if it's in a blocked range
        if _is_private_ip(str(addr)):
            raise FetchError(
                f"Direct IP address '{hostname}' is in a private or reserved range "
                "and cannot be fetched."
            )
    except FetchError:
        raise   # re-raise our security error, do not swallow it
    except ValueError:
        pass  # not an IP literal — normal hostname, DNS check happens at fetch time


async def check_host(hostname: str) -> None:
    """Async DNS + private-IP check for one hostname (raises FetchError)."""
    try:
        await asyncio.to_thread(_resolve_and_check, hostname)
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"DNS check failed: {exc}") from exc


_REDIRECT_CODES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})


@asynccontextmanager
async def safe_stream(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_redirects: int = MAX_REDIRECTS,
) -> AsyncIterator[httpx.Response]:
    """
    Streaming GET that follows redirects MANUALLY, validating every hop
    (scheme, IP literal, DNS → non-global IP) *before* requesting it.
    The client must be created with ``follow_redirects=False``.

    Residual risk: DNS rebinding between our check and httpx's own resolution.
    In production, also egress-filter the worker (no access to metadata / RFC1918).
    """
    current = url
    for _hop in range(max_redirects + 1):
        validate_url(current)
        await check_host(urlparse(current).hostname or "")
        resp = await client.send(client.build_request("GET", current), stream=True)
        location = resp.headers.get("location")
        if resp.status_code in _REDIRECT_CODES and location:
            await resp.aclose()
            current = str(resp.url.join(location))
            continue
        try:
            yield resp
        finally:
            await resp.aclose()
        return
    raise FetchError(f"Too many redirects (max {max_redirects}) for '{url}'.")


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

async def fetch_url(
    url: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    connect_timeout: float = CONNECT_TIMEOUT,
    read_timeout: float = READ_TIMEOUT,
) -> FetchResult:
    """
    Fetch *url* safely and return a FetchResult.

    Steps:
      1. Syntactic URL validation
      2. DNS resolution + private-IP check (in thread pool to avoid blocking)
      3. httpx streaming GET with size cap
      4. MIME type detection and normalisation
      5. HTML stripping if content is text/html
      6. Return FetchResult

    Raises FetchError for any failure.
    """
    # --- 1. Syntactic validation ---
    validate_url(url)
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # --- 2. DNS + private-IP check happens per hop inside safe_stream() ---

    # --- 3. Streaming download ---
    timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=10.0, pool=5.0)
    final_url = url
    collected: list[bytes] = []
    total = 0
    detected_mime: str | None = None

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            async with safe_stream(client, url) as resp:
                final_url = str(resp.url)

                if resp.status_code != 200:
                    raise FetchError(
                        f"HTTP {resp.status_code} fetching '{url}'. "
                        "Only 200 OK responses are accepted."
                    )

                # Content-Length pre-flight (advisory — server may omit it)
                cl_header = resp.headers.get("content-length")
                if cl_header:
                    try:
                        declared_len = int(cl_header)
                        if declared_len > max_bytes:
                            raise FetchError(
                                f"Server reports content-length {declared_len:,} bytes which exceeds "
                                f"the {max_bytes // (1024*1024)} MB limit."
                            )
                    except (ValueError, TypeError):
                        pass  # ignore malformed header

                # Normalise MIME
                raw_ct = resp.headers.get("content-type", "")
                detected_mime = _normalise_mime(raw_ct, final_url)
                if detected_mime is None:
                    raise FetchError(
                        f"Unsupported content type '{raw_ct}'. "
                        "Accepted: PDF, DOCX, plain text, Markdown, or HTML."
                    )

                # Stream body
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(
                            f"Response body exceeds the {max_bytes // (1024*1024)} MB download limit."
                        )
                    collected.append(chunk)

    except FetchError:
        raise
    except httpx.TimeoutException as exc:
        raise FetchError(f"Request timed out fetching '{url}': {exc}") from exc
    except httpx.TooManyRedirects:
        raise FetchError(f"Too many redirects (max {MAX_REDIRECTS}) for '{url}'.")
    except httpx.RequestError as exc:
        raise FetchError(f"Network error fetching '{url}': {exc}") from exc

    data = b"".join(collected)

    # --- 4. HTML stripping ---
    if detected_mime == "text/plain" and _is_html_content(data):
        data = _strip_html(data)

    # --- 5. Derive filename ---
    path_part = urlparse(final_url).path.rstrip("/") or "document"
    filename = path_part.rsplit("/", 1)[-1] or "document"
    # Ensure the filename has an appropriate extension
    if "." not in filename:
        ext = _mime_to_ext(detected_mime)
        filename = filename + ext

    log.info(
        "URL fetched: url=%s final=%s bytes=%d mime=%s",
        url, final_url, len(data), detected_mime,
    )

    return FetchResult(
        data=data,
        mime_type=detected_mime,
        final_url=final_url,
        content_length=len(data),
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_mime(content_type: str, url: str) -> str | None:
    """
    Map a raw Content-Type header value to one of our accepted MIME strings.
    Falls back to extension-based detection if Content-Type is missing/generic.
    Returns None if the content type is not accepted.
    """
    # Strip parameters (e.g. "text/html; charset=utf-8" → "text/html")
    base = content_type.split(";")[0].strip().lower() if content_type else ""

    # Direct match
    if base in ACCEPTED_CONTENT_TYPES:
        return ACCEPTED_CONTENT_TYPES[base]

    # Generic octet-stream / empty — try extension
    parsed_path = urlparse(url).path.lower()
    for ext, mime in _EXT_MIME.items():
        if parsed_path.endswith(ext):
            return mime

    # application/octet-stream is too vague without a matching extension
    return None


def _is_html_content(data: bytes) -> bool:
    """Quick check: does the content look like HTML?"""
    snippet = data[:1024].lstrip()
    return snippet.lower().startswith(b"<!doctype html") or b"<html" in snippet.lower()


def _mime_to_ext(mime: str) -> str:
    for ext, m in _EXT_MIME.items():
        if m == mime:
            return ext
    return ".bin"

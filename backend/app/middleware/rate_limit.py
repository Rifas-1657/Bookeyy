"""
app/middleware/rate_limit.py — Per-user / per-IP rate limiting via slowapi.

Wired in app/main.py with SlowAPIMiddleware, so EVERY HTTP route gets the
default limit (RATE_LIMIT_PER_MINUTE). Stricter per-route limits:

  - Auth (/auth/login, /auth/signup, /auth/password-reset/*): RATE_LIMIT_AUTH_PER_MINUTE
  - POST /api/books:                                          RATE_LIMIT_BOOKS_PER_MINUTE
  - /api/voice/tts, /api/voice/stt, /api/voice/intent:        RATE_LIMIT_VOICE_PER_MINUTE

Key: "user:<id>" when the request carries a valid access JWT (Bearer or
access_token cookie), else "ip:<client ip>". X-Forwarded-For is honoured ONLY
when the direct peer is listed in TRUSTED_PROXIES (comma-separated IPs/CIDRs);
otherwise a client could spoof it to dodge limits.

Storage is in-memory (single process). With several workers, configure a shared
backend (e.g. Limiter(storage_uri="redis://...")).
Set RATE_LIMIT_ENABLED=false to disable (the test-suite does this by default).
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from slowapi import Limiter
from starlette.requests import Request

from app.config import get_settings


@lru_cache(maxsize=8)
def _trusted_networks(raw: str) -> tuple:
    nets = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted_proxy(host: str) -> bool:
    nets = _trusted_networks(get_settings().trusted_proxies)
    if not nets or not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if _is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for", "")
        # Walk right-to-left, skipping our own trusted proxies; first untrusted hop is the client.
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        for hop in reversed(hops):
            if not _is_trusted_proxy(hop):
                return hop
        if hops:
            return hops[0]
    return peer or "unknown"


def _user_id_from_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get("access_token")
    if not token:
        return None
    try:
        from app.auth.service import decode_token
        payload = decode_token(token)
    except Exception:
        return None
    if payload.get("type") != "access":
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


def rate_limit_key(request: Request) -> str:
    uid = _user_id_from_token(request)
    return f"user:{uid}" if uid else f"ip:{client_ip(request)}"


def default_limit() -> str:
    return f"{max(1, get_settings().rate_limit_per_minute)}/minute"


def auth_limit() -> str:
    return f"{max(1, get_settings().rate_limit_auth_per_minute)}/minute"


def books_limit() -> str:
    return f"{max(1, get_settings().rate_limit_books_per_minute)}/minute"


def voice_limit() -> str:
    return f"{max(1, get_settings().rate_limit_voice_per_minute)}/minute"


limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=[default_limit],
    enabled=get_settings().rate_limit_enabled,
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
# slowapi's SlowAPIMiddleware resolves the endpoint via `route.endpoint`, which this
# FastAPI version's included-router wrappers do not expose — it silently skipped every
# route. This subclass resolves the endpoint through Starlette's own matching.

from slowapi.middleware import SlowAPIMiddleware, _should_exempt, sync_check_limits  # noqa: E402
from starlette.routing import Match  # noqa: E402

_EXEMPT_PATHS = frozenset({"/health", "/ready"})


def find_endpoint(routes, scope):
    """Endpoint function for this request, or None if nothing matches."""
    for route in routes:
        try:
            match, child = route.matches(scope)
        except Exception:
            continue
        if match != Match.FULL:
            continue
        endpoint = (child or {}).get("endpoint") or getattr(route, "endpoint", None)
        if endpoint is None:
            inner = getattr(getattr(route, "original_router", None), "routes", None) or getattr(route, "routes", None)
            if inner:
                endpoint = find_endpoint(inner, {**scope, **(child or {})})
        return endpoint or _unresolved_endpoint
    return None


def _unresolved_endpoint() -> None:  # pragma: no cover - placeholder key for default limits
    """Matched route whose endpoint could not be resolved: still gets the default limit."""


class RateLimitMiddleware(SlowAPIMiddleware):
    async def dispatch(self, request, call_next):
        app = request.app
        lim: Limiter = app.state.limiter
        if not lim.enabled or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        handler = find_endpoint(app.routes, request.scope)
        if handler is None or _should_exempt(lim, handler):
            return await call_next(request)
        error_response, inject = sync_check_limits(lim, request, handler, app)
        if error_response is not None:
            return error_response
        response = await call_next(request)
        if inject:
            response = lim._inject_headers(response, request.state.view_rate_limit)
        return response

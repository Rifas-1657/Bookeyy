"""
app/middleware/security.py — Security headers + request size enforcement.

Adds:
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  X-XSS-Protection: 0  (disabled — modern browsers use CSP)
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(self), geolocation=()
  Content-Security-Policy: default-src 'self'; ... (API-safe minimal policy)
  Strict-Transport-Security: max-age=63072000; includeSubDomains (production only)

Never returns: stack traces, Python exception details, or internal paths.
"""
from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

_settings = get_settings()

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(self), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = _CSP
        if _settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


# ---------------------------------------------------------------------------
# Request body size limit (pure ASGI — runs before any body is buffered)
# ---------------------------------------------------------------------------

_MULTIPART_OVERHEAD = 256 * 1024  # form boundaries + small text fields


def _body_limit_for(path: str) -> int | None:
    """Max request body for upload endpoints; None = not limited here."""
    s = get_settings()
    if path.startswith("/api/voice/stt"):
        return s.max_stt_upload_bytes + _MULTIPART_OVERHEAD
    if path.startswith("/api/admin/documents"):
        return s.max_admin_upload_body_bytes
    if path == "/api/books/media":
        # the only route with a raised limit (video / audio / batches of images)
        if s.max_media_upload_body_bytes > 0:
            return s.max_media_upload_body_bytes
        from app.books.limits import get_media_limits
        return get_media_limits().body_bytes
    if path == "/api/books" or path.startswith("/api/books/"):
        return s.max_upload_body_bytes
    return None


class BodySizeLimitMiddleware:
    """Reject oversized upload bodies early.

    1. Declared Content-Length above the limit → 413 before reading anything.
    2. Chunked / undeclared bodies are counted as they stream; once the limit is
       crossed the request is aborted with 413 (HTTPException raised from receive()).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        limit = _body_limit_for(scope.get("path", "").rstrip("/") or "/")
        if limit is None:
            return await self.app(scope, receive, send)

        declared = None
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    declared = int(v)
                except ValueError:
                    declared = None
                break
        if declared is not None and declared > limit:
            return await self._reject(send, limit)

        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    from starlette.exceptions import HTTPException as _HTTPExc
                    raise _HTTPExc(status_code=413, detail=_detail(limit))
            return message

        async def tracking_send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 413 and not started:
                return await self._reject(send, limit)
            raise

    @staticmethod
    async def _reject(send, limit: int) -> None:
        import json as _json
        body = _json.dumps({"detail": _detail(limit)}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _detail(limit: int) -> str:
    return f"Request body exceeds the {max(1, limit // (1024 * 1024))} MB upload limit"

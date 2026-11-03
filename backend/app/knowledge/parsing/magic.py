"""
app/knowledge/parsing/magic.py

Magic-byte verification for the four allowed document types.
Never trust extensions alone — verify file signatures.

Signatures:
  PDF  : %PDF  (25 50 44 46)
  DOCX : PK zip magic (50 4B 03 04)  — Office Open XML is a ZIP
  PPTX : same ZIP magic; told apart from DOCX by the ppt/ part inside the ZIP
  MD   : UTF-8 text (no magic; heuristic: printable ASCII)
  TXT  : UTF-8 text (same heuristic)

MIME types we declare for each:
  PDF  → application/pdf
  DOCX → application/vnd.openxmlformats-officedocument.wordprocessingml.document
  MD   → text/markdown
  TXT  → text/plain
"""
from __future__ import annotations

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".txt"}
ALLOWED_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    PPTX_MIME,
    "text/markdown",
    "text/plain",
}

_PDF_MAGIC  = b"%PDF"
_DOCX_MAGIC = b"PK\x03\x04"     # ZIP magic (OOXML)


def detect_mime(header: bytes) -> str | None:
    """
    Detect MIME type from the first 512 bytes of file content.
    Returns the MIME string or None if not recognized/allowed.
    """
    if header[:4] == _PDF_MAGIC:
        return "application/pdf"
    if header[:4] == _DOCX_MAGIC:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    # Text heuristic: if first 512 bytes are mostly printable UTF-8, treat as text
    try:
        sample = header[:512].decode("utf-8", errors="strict")
        # Reject if high fraction of control chars (binary data)
        ctrl = sum(1 for c in sample if ord(c) < 9 or (11 <= ord(c) < 32))
        if ctrl / max(len(sample), 1) < 0.05:
            return "text/plain"  # caller refines to text/markdown by extension
    except UnicodeDecodeError:
        pass
    return None


def verify_magic(data: bytes, filename: str) -> tuple[bool, str, str | None]:
    """
    Verify magic bytes and return (ok, mime, error_message).
    Also maps text/plain + .md extension → text/markdown.
    """
    import os
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, "", f"File extension '{ext}' is not allowed. Use: pdf, docx, pptx, md, txt"

    header = data[:512]
    detected = detect_mime(header)

    if detected is None:
        return False, "", "File content does not match any allowed type (PDF, DOCX, PPTX, MD, TXT)"

    # OOXML: DOCX and PPTX share the ZIP magic — look inside the archive
    if detected == DOCX_MIME:
        bomb = check_zip_safety(data)
        if bomb:
            return False, "", bomb
        detected = _ooxml_kind(data) or detected

    # Reclassify plain text as markdown when extension says so
    if detected == "text/plain" and ext == ".md":
        detected = "text/markdown"

    # Cross-check: extension must agree with detected magic
    ext_to_expected = {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": PPTX_MIME,
        ".md":   "text/markdown",
        ".txt":  "text/plain",
    }
    expected = ext_to_expected[ext]
    if detected != expected:
        return False, "", (
            f"File extension '{ext}' does not match file content "
            f"(detected: {detected})"
        )

    return True, detected, None


def _ooxml_kind(data: bytes) -> str | None:
    """Return the PPTX/DOCX mime for a valid OOXML zip, or None if it can't be read."""
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except Exception:
        return None
    if any(n.startswith("ppt/") for n in names):
        return PPTX_MIME
    if any(n.startswith("word/") for n in names):
        return DOCX_MIME
    return None


_RATIO_MIN_ENTRY_BYTES = 1024 * 1024  # small XML parts compress well; only judge big ones


def check_zip_safety(
    data: bytes,
    *,
    max_total: int | None = None,
    max_entries: int | None = None,
    max_ratio: float | None = None,
) -> str | None:
    """Zip-bomb guard for OOXML (docx/pptx). Reads only the central directory.
    Returns an error message, or None if the archive looks safe to decompress."""
    import io
    import zipfile

    from app.config import get_settings
    s = get_settings()
    max_total = max_total or s.zip_max_total_uncompressed_bytes
    max_entries = max_entries or s.zip_max_entries
    max_ratio = max_ratio or s.zip_max_compression_ratio
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
    except Exception:
        return None  # not a readable zip — the parser will report it
    if len(infos) > max_entries:
        return f"Archive has too many entries ({len(infos)} > {max_entries})."
    total = 0
    total_compressed = 0
    for info in infos:
        total += max(0, info.file_size)
        total_compressed += max(0, info.compress_size)
        if total > max_total:
            return (f"Archive expands to more than {max_total // (1024 * 1024)} MB "
                    "when decompressed (possible zip bomb).")
        if info.file_size >= _RATIO_MIN_ENTRY_BYTES and info.file_size / max(1, info.compress_size) > max_ratio:
            return (f"Archive entry '{info.filename[:80]}' has a suspicious compression ratio "
                    "(possible zip bomb).")
    if total >= _RATIO_MIN_ENTRY_BYTES and total / max(1, total_compressed) > max_ratio:
        return "Archive has a suspicious overall compression ratio (possible zip bomb)."
    return None

"""Helpers: PDF first page → PNG bytes for vision APIs."""

from __future__ import annotations


def pdf_conversion_available() -> bool:
    """True if PyMuPDF (`pip install pymupdf`) is installed."""
    try:
        import fitz  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def pdf_first_page_as_png(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    try:
        import fitz  # PyMuPDF
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Для PDF нужен пакет PyMuPDF. Выполните: pip install pymupdf"
        ) from e

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(0)
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def guess_mime_from_upload(name: str, default: str) -> str:
    lower = name.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return default

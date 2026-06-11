"""
parser.py
---------
Extracts plain text from uploaded files.
Returns a list of (page_or_chunk_index, text) tuples so callers
can track where each piece of text came from.

Supported: .pdf  .docx  .txt  .csv
"""

import io
from typing import List, Tuple


def parse_file(filename: str, content: bytes) -> List[Tuple[int, str]]:
    """
    Returns a list of (page_index, text) pairs.
    For non-paginated formats (txt, csv, docx) page_index is always 0.
    """
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext == "pdf":
        return _parse_pdf(content)
    elif ext == "docx":
        return _parse_docx(content)
    elif ext == "txt":
        return [(0, content.decode("utf-8", errors="replace"))]
    elif ext == "csv":
        return [(0, content.decode("utf-8", errors="replace"))]
    else:
        return []


def _parse_pdf(content: bytes) -> List[Tuple[int, str]]:
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(content))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((i + 1, text))  # 1-based page numbers
        return pages
    except Exception as e:
        print(f"[parser] PDF error: {e}")
        return []


def _parse_docx(content: bytes) -> List[Tuple[int, str]]:
    try:
        from docx import Document
        doc = Document(io.BytesIO(content))
        full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return [(0, full_text)]
    except Exception as e:
        print(f"[parser] DOCX error: {e}")
        return []
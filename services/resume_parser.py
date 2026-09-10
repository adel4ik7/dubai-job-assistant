from pathlib import Path

from docx import Document
from pypdf import PdfReader


class ResumeParseError(Exception):
    pass


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif suffix == ".docx":
        doc = Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
    elif suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="ignore")
    else:
        raise ResumeParseError("Supported formats: PDF, DOCX, TXT.")

    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) < 100:
        raise ResumeParseError(
            "I could not extract enough text. The PDF may be scanned as an image."
        )
    return text

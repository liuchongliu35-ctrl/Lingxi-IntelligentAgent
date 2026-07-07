from __future__ import annotations

from pathlib import Path


class DocumentParser:
    """Read local txt, md, and pdf files."""

    def run(self, file_path: str) -> str:
        path = Path(file_path).resolve()
        if not path.exists():
            return f"File does not exist: {path}"

        suffix = path.suffix.lower()
        if suffix in {".txt", ".md"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".pdf":
            return self._parse_pdf(path)
        return f"Unsupported document type: {suffix}"

    def _parse_pdf(self, path: Path) -> str:
        try:
            import PyPDF2
        except ImportError:
            return "PyPDF2 is not installed."

        content: list[str] = []
        with path.open("rb") as handle:
            reader = PyPDF2.PdfReader(handle)
            for page in reader.pages:
                content.append(page.extract_text() or "")
        return "\n".join(content).strip()

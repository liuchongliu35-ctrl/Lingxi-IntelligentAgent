from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from io import StringIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from src.core.config import get_settings
from src.tools.base import ToolResult
from src.tools.data_types import DocumentParseData
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallOptions

from .file_tools.path_resolver import PathResolver, ResolvedPath


DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_CHARS = 20_000
HARD_MAX_CHARS = 200_000
DOCUMENT_MAX_BYTES = 8 * 1024 * 1024
OOXML_MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
TEXT_PREVIEW_CHARS = 4_000
CSV_MAX_ROWS = 200
CSV_MAX_COLUMNS = 50
XLSX_MAX_ROWS = 200
XLSX_MAX_COLUMNS = 50
FALLBACK_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp1252")
SUPPORTED_SUFFIXES = {".txt", ".md", ".json", ".csv", ".pdf", ".docx", ".xlsx"}


class DocumentParser:
    """Parse workspace documents into structured DocumentParseData."""

    def run(
        self,
        path: str | None = None,
        file_path: str | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_chars: int = DEFAULT_MAX_CHARS,
        include_metadata: bool = True,
        *,
        workspace_root: str | Path | None = None,
        tool_call_options: ToolCallOptions | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        target = path or file_path
        if not isinstance(target, str) or not target.strip():
            return ToolResult.fail(
                "path is required.",
                code=ToolErrorCode.MISSING_REQUIRED_PARAM.value,
                data={"path": target, "file_path": file_path},
            )

        root = _workspace_root(workspace_root)
        resolved = PathResolver(root).resolve(target)
        allow_sensitive = bool(
            resolved.is_sensitive
            and tool_call_options is not None
            and tool_call_options.has_confirmation_ticket
        )
        validation = _validate_document_target(resolved, allow_sensitive=allow_sensitive)
        if validation is not None:
            return validation

        file = Path(resolved.path_resolved)
        suffix = file.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return _failure(
                f"Unsupported document type: {suffix or '<none>'}",
                ToolErrorCode.UNSUPPORTED_DOCUMENT_TYPE.value,
                resolved,
                file_type=suffix.lstrip("."),
                metadata={"supported_extensions": sorted(SUPPORTED_SUFFIXES)},
            )

        size_bytes = file.stat().st_size
        if size_bytes > DOCUMENT_MAX_BYTES:
            return _failure(
                f"Document is too large to parse: {resolved.workspace_relative_path}",
                ToolErrorCode.DOCUMENT_TOO_LARGE.value,
                resolved,
                file_type=suffix.lstrip("."),
                metadata={
                    "size_bytes": size_bytes,
                    "max_size_bytes": DOCUMENT_MAX_BYTES,
                },
            )

        limits = {
            "max_pages": _positive_int(max_pages, DEFAULT_MAX_PAGES),
            "max_chars": _bounded_int(max_chars, DEFAULT_MAX_CHARS, HARD_MAX_CHARS),
            "include_metadata": bool(include_metadata),
        }
        try:
            if suffix in {".txt", ".md"}:
                parsed = _parse_plain_text(file)
            elif suffix == ".json":
                parsed = _parse_json(file)
            elif suffix == ".csv":
                parsed = _parse_csv(file)
            elif suffix == ".pdf":
                parsed = _parse_pdf(file, limits["max_pages"])
            elif suffix == ".docx":
                parsed = _parse_docx(file)
            elif suffix == ".xlsx":
                parsed = _parse_xlsx(file)
            else:
                parsed = {"text": "", "metadata": {}, "tables": [], "parser": None}
        except _DependencyMissing as exc:
            return _failure(
                str(exc),
                ToolErrorCode.DEPENDENCY_NOT_AVAILABLE.value,
                resolved,
                file_type=suffix.lstrip("."),
                metadata={"dependency": exc.dependency},
            )
        except _DocumentEncrypted as exc:
            return _failure(
                str(exc),
                ToolErrorCode.DOCUMENT_ENCRYPTED.value,
                resolved,
                file_type=suffix.lstrip("."),
                metadata={"parser": exc.parser},
            )
        except (
            OSError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
            ElementTree.ParseError,
            csv.Error,
        ) as exc:
            return _failure(
                f"Unable to parse document: {resolved.workspace_relative_path}",
                ToolErrorCode.DOCUMENT_PARSE_FAILED.value,
                resolved,
                file_type=suffix.lstrip("."),
                metadata={"parse_error": str(exc), "error_type": type(exc).__name__},
            )

        text = str(parsed.get("text") or "")
        limited_text, text_truncated = _limit_text(text, limits["max_chars"])
        metadata = {
            "size_bytes": size_bytes,
            "text_chars": len(text),
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "max_chars": limits["max_chars"],
            "max_pages": limits["max_pages"],
            "source_path_argument": "path" if path else "file_path",
            **dict(parsed.get("metadata") or {}),
        }
        if not limits["include_metadata"]:
            metadata = {}

        data = DocumentParseData(
            path=resolved.workspace_relative_path or "",
            file_type=suffix.lstrip("."),
            title=parsed.get("title"),
            page_count=parsed.get("page_count"),
            sheet_count=parsed.get("sheet_count"),
            text=limited_text,
            text_preview=_preview(limited_text, TEXT_PREVIEW_CHARS),
            text_truncated=text_truncated,
            tables=list(parsed.get("tables") or []),
            metadata=metadata,
            parser=parsed.get("parser"),
        ).to_dict()
        return ToolResult.ok(
            data=data,
            message=f"Parsed document {resolved.workspace_relative_path}.",
        )


class _DependencyMissing(Exception):
    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        super().__init__(f"Dependency is not available: {dependency}")


class _DocumentEncrypted(Exception):
    def __init__(self, parser: str) -> None:
        self.parser = parser
        super().__init__("Document is encrypted and cannot be parsed.")


def _validate_document_target(
    resolved: ResolvedPath,
    *,
    allow_sensitive: bool,
) -> ToolResult | None:
    if not resolved.valid or not resolved.is_inside_workspace:
        return ToolResult.fail(
            resolved.reason or "Invalid workspace path.",
            code=resolved.error_code,
            data=resolved.to_dict(),
        )
    if resolved.is_blocked and not allow_sensitive:
        return ToolResult.fail(
            resolved.reason or "Document path is blocked by policy.",
            code=resolved.error_code,
            data=resolved.to_dict(),
        )
    if not resolved.exists:
        return ToolResult.fail(
            f"Path not found: {resolved.workspace_relative_path}",
            code=ToolErrorCode.FILE_NOT_FOUND.value,
            data=_base_info(resolved),
        )
    if resolved.resource_type != "file":
        return ToolResult.fail(
            f"Path is not a file: {resolved.workspace_relative_path}",
            code=ToolErrorCode.NOT_A_FILE.value,
            data=_base_info(resolved),
        )
    if resolved.is_symlink:
        return ToolResult.fail(
            f"Symlink document reads are not supported: {resolved.workspace_relative_path}",
            code=ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            data=_base_info(resolved),
        )
    return None


def _parse_plain_text(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text, encoding = _decode_text(raw)
    return {
        "text": text,
        "metadata": {"encoding": encoding, "line_count": _line_count(text)},
        "tables": [],
        "parser": "stdlib_text",
    }


def _parse_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text, encoding = _decode_text(raw)
    value = json.loads(text or "null")
    formatted = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    metadata = {
        "encoding": encoding,
        "json_type": type(value).__name__,
    }
    if isinstance(value, dict):
        metadata["top_level_keys"] = sorted(str(key) for key in value.keys())[:100]
        metadata["top_level_count"] = len(value)
    elif isinstance(value, list):
        metadata["top_level_count"] = len(value)
    return {
        "text": formatted,
        "metadata": metadata,
        "tables": [],
        "parser": "stdlib_json",
    }


def _parse_csv(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text, encoding = _decode_text(raw)
    stream = StringIO(text)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    rows: list[list[str]] = []
    total_rows = 0
    max_columns = 0
    for row in csv.reader(stream, dialect):
        total_rows += 1
        normalized = [str(cell) for cell in row[:CSV_MAX_COLUMNS]]
        max_columns = max(max_columns, len(row))
        if len(rows) < CSV_MAX_ROWS:
            rows.append(normalized)
    text_lines = [", ".join(row) for row in rows]
    return {
        "text": "\n".join(text_lines),
        "metadata": {
            "encoding": encoding,
            "row_count": total_rows,
            "column_count": max_columns,
            "rows_returned": len(rows),
            "row_limit": CSV_MAX_ROWS,
            "column_limit": CSV_MAX_COLUMNS,
            "table_truncated": total_rows > len(rows) or max_columns > CSV_MAX_COLUMNS,
        },
        "tables": [
            {
                "name": path.stem,
                "rows": rows,
                "row_count": total_rows,
                "column_count": max_columns,
                "truncated": total_rows > len(rows) or max_columns > CSV_MAX_COLUMNS,
            }
        ],
        "parser": "stdlib_csv",
    }


def _parse_pdf(path: Path, max_pages: int) -> dict[str, Any]:
    try:
        import PyPDF2  # type: ignore
    except ImportError as exc:
        raise _DependencyMissing("PyPDF2") from exc
    with path.open("rb") as handle:
        reader = PyPDF2.PdfReader(handle)
        if getattr(reader, "is_encrypted", False):
            raise _DocumentEncrypted("PyPDF2")
        pages = list(reader.pages)
        selected = pages[:max_pages]
        text = "\n".join((page.extract_text() or "") for page in selected)
    return {
        "text": text,
        "page_count": len(pages),
        "metadata": {
            "pages_returned": len(selected),
            "page_limit": max_pages,
            "pages_truncated": len(pages) > len(selected),
        },
        "tables": [],
        "parser": "PyPDF2",
    }


def _parse_docx(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        _validate_zip_budget(archive)
        if any(name.startswith("vbaProject") or name.endswith("/vbaProject.bin") for name in archive.namelist()):
            raise ValueError("macro-enabled document content is not supported")
        try:
            document_xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise zipfile.BadZipFile("word/document.xml missing") from exc
    root = ElementTree.fromstring(document_xml)
    paragraphs: list[str] = []
    table_count = 0
    for paragraph in root.findall(".//w:p", _DOCX_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", _DOCX_NS))
        if text:
            paragraphs.append(text)
    for _table in root.findall(".//w:tbl", _DOCX_NS):
        table_count += 1
    return {
        "text": "\n".join(paragraphs),
        "metadata": {
            "paragraph_count": len(paragraphs),
            "table_count": table_count,
        },
        "tables": [],
        "parser": "stdlib_docx_xml",
    }


def _parse_xlsx(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        _validate_zip_budget(archive)
        names = set(archive.namelist())
        shared_strings = _xlsx_shared_strings(archive) if "xl/sharedStrings.xml" in names else []
        sheet_paths = sorted(
            name
            for name in names
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        tables: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for index, sheet_path in enumerate(sheet_paths, start=1):
            table = _parse_xlsx_sheet(
                archive.read(sheet_path),
                shared_strings,
                name=f"sheet{index}",
            )
            tables.append(table)
            text_parts.extend(", ".join(row) for row in table["rows"])
    return {
        "text": "\n".join(text_parts),
        "sheet_count": len(sheet_paths),
        "metadata": {
            "sheet_count": len(sheet_paths),
            "sheets_returned": len(tables),
            "row_limit": XLSX_MAX_ROWS,
            "column_limit": XLSX_MAX_COLUMNS,
        },
        "tables": tables,
        "parser": "stdlib_xlsx_xml",
    }


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(".//main:si", _XLSX_NS):
        values.append("".join(node.text or "" for node in item.findall(".//main:t", _XLSX_NS)))
    return values


def _parse_xlsx_sheet(
    xml: bytes,
    shared_strings: list[str],
    *,
    name: str,
) -> dict[str, Any]:
    root = ElementTree.fromstring(xml)
    rows: list[list[str]] = []
    total_rows = 0
    max_columns = 0
    for row in root.findall(".//main:row", _XLSX_NS):
        total_rows += 1
        values: list[str] = []
        for cell in row.findall("main:c", _XLSX_NS):
            values.append(_xlsx_cell_text(cell, shared_strings))
        max_columns = max(max_columns, len(values))
        if len(rows) < XLSX_MAX_ROWS:
            rows.append(values[:XLSX_MAX_COLUMNS])
    return {
        "name": name,
        "rows": rows,
        "row_count": total_rows,
        "column_count": max_columns,
        "truncated": total_rows > len(rows) or max_columns > XLSX_MAX_COLUMNS,
    }


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", _XLSX_NS))
    value = cell.find("main:v", _XLSX_NS)
    raw = value.text if value is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return ""
    return raw or ""


def _validate_zip_budget(archive: zipfile.ZipFile) -> None:
    total = 0
    for info in archive.infolist():
        total += max(int(info.file_size), 0)
        if total > OOXML_MAX_UNCOMPRESSED_BYTES:
            raise ValueError("OOXML uncompressed size exceeds limit")


def _failure(
    message: str,
    code: str,
    resolved: ResolvedPath,
    *,
    file_type: str,
    metadata: dict[str, Any],
) -> ToolResult:
    return ToolResult.fail(
        message,
        code=code,
        data={
            "path": resolved.workspace_relative_path,
            "file_type": file_type,
            "title": None,
            "page_count": None,
            "sheet_count": None,
            "text": None,
            "text_preview": None,
            "text_truncated": False,
            "tables": [],
            "metadata": metadata,
            "parser": metadata.get("parser"),
        },
    )


def _decode_text(raw: bytes) -> tuple[str, str]:
    errors: list[str] = []
    for encoding in FALLBACK_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise UnicodeError("; ".join(errors))


def _limit_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    suffix = f"\n... [truncated {len(text) - max_chars} chars]"
    if len(suffix) >= max_chars:
        return suffix[:max_chars], True
    return text[: max_chars - len(suffix)] + suffix, True


def _preview(text: str, max_chars: int) -> str:
    return _limit_text(text, max_chars)[0]


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return max(normalized, 1)


def _bounded_int(value: Any, default: int, maximum: int) -> int:
    return min(_positive_int(value, default), maximum)


def _base_info(resolved: ResolvedPath) -> dict[str, Any]:
    return {
        "path": resolved.workspace_relative_path,
        "type": resolved.resource_type,
        "exists": resolved.exists,
        "is_sensitive": resolved.is_sensitive,
        "is_ignored": resolved.is_ignored,
        "is_symlink": resolved.is_symlink,
    }


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_XLSX_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_PAGES",
    "DOCUMENT_MAX_BYTES",
    "DocumentParser",
]

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.document_parser import DOCUMENT_MAX_BYTES, DocumentParser
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.tool_manager import ToolManager


class DocumentParserV1Test(unittest.TestCase):
    def test_txt_and_markdown_parse_to_structured_data(self):
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "notes.md"
            path.write_text("# Title\nbody\n", encoding="utf-8")

            result = _execute(workspace, {"path": "notes.md"})

            self.assertTrue(result.success)
            self.assertEqual(result.data["path"], "notes.md")
            self.assertEqual(result.data["file_type"], "md")
            self.assertEqual(result.data["text"].replace("\r\n", "\n"), "# Title\nbody\n")
            self.assertEqual(result.data["metadata"]["line_count"], 2)
            self.assertEqual(result.data["parser"], "stdlib_text")

    def test_file_path_compatibility_remains_available(self):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "legacy.txt").write_text("legacy", encoding="utf-8")

            result = DocumentParser().run(file_path="legacy.txt", workspace_root=workspace)

            self.assertTrue(result.success)
            self.assertEqual(result.data["path"], "legacy.txt")
            self.assertEqual(result.data["metadata"]["source_path_argument"], "file_path")

    def test_json_preserves_type_metadata_and_formats_text(self):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "data.json").write_text('{"b": 2, "a": 1}', encoding="utf-8")

            result = _execute(workspace, {"path": "data.json"})

            self.assertTrue(result.success)
            self.assertEqual(result.data["file_type"], "json")
            self.assertEqual(result.data["metadata"]["json_type"], "dict")
            self.assertEqual(result.data["metadata"]["top_level_keys"], ["a", "b"])
            self.assertIn('"a": 1', result.data["text"])

    def test_csv_returns_bounded_table_data(self):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "table.csv").write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")

            result = _execute(workspace, {"path": "table.csv"})

            self.assertTrue(result.success)
            self.assertEqual(result.data["file_type"], "csv")
            self.assertEqual(result.data["tables"][0]["rows"][1], ["alpha", "1"])
            self.assertEqual(result.data["metadata"]["row_count"], 3)

    def test_docx_minimal_ooxml_is_parsed_without_model_or_rag(self):
        with tempfile.TemporaryDirectory() as workspace:
            _write_minimal_docx(Path(workspace) / "doc.docx", ["Hello", "World"])

            result = _execute(workspace, {"path": "doc.docx"})

            self.assertTrue(result.success)
            self.assertEqual(result.data["file_type"], "docx")
            self.assertEqual(result.data["text"], "Hello\nWorld")
            self.assertEqual(result.data["parser"], "stdlib_docx_xml")

    def test_xlsx_minimal_ooxml_returns_sheet_table(self):
        with tempfile.TemporaryDirectory() as workspace:
            _write_minimal_xlsx(Path(workspace) / "book.xlsx")

            result = _execute(workspace, {"path": "book.xlsx"})

            self.assertTrue(result.success)
            self.assertEqual(result.data["file_type"], "xlsx")
            self.assertEqual(result.data["sheet_count"], 1)
            self.assertEqual(result.data["tables"][0]["rows"][0], ["Name", "Value"])
            self.assertIn("Alpha", result.data["text"])

    def test_max_chars_truncates_text_but_keeps_hash_metadata(self):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "long.txt").write_text("abcdef", encoding="utf-8")

            result = _execute(workspace, {"path": "long.txt", "max_chars": 3})

            self.assertTrue(result.success)
            self.assertTrue(result.data["text_truncated"])
            self.assertLessEqual(len(result.data["text"]), 3)
            self.assertEqual(result.data["metadata"]["text_chars"], 6)
            self.assertIn("text_hash", result.data["metadata"])

    def test_include_metadata_false_suppresses_metadata(self):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "notes.txt").write_text("body", encoding="utf-8")

            result = _execute(workspace, {"path": "notes.txt", "include_metadata": False})

            self.assertTrue(result.success)
            self.assertEqual(result.data["metadata"], {})

    def test_unsupported_corrupt_large_and_workspace_escape_are_structured(self):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "demo.bin").write_bytes(b"demo")
            Path(workspace, "bad.docx").write_bytes(b"not a zip")
            large = Path(workspace, "large.txt")
            with large.open("wb") as handle:
                handle.seek(DOCUMENT_MAX_BYTES)
                handle.write(b"x")

            unsupported = _execute(workspace, {"path": "demo.bin"})
            corrupt = _execute(workspace, {"path": "bad.docx"})
            too_large = _execute(workspace, {"path": "large.txt"})
            outside = _execute(workspace, {"path": str(Path(workspace).parent / "outside.txt")})

            self.assertFalse(unsupported.success)
            self.assertEqual(unsupported.code, ToolErrorCode.UNSUPPORTED_DOCUMENT_TYPE.value)
            self.assertFalse(corrupt.success)
            self.assertEqual(corrupt.code, ToolErrorCode.DOCUMENT_PARSE_FAILED.value)
            self.assertFalse(too_large.success)
            self.assertEqual(too_large.code, ToolErrorCode.DOCUMENT_TOO_LARGE.value)
            self.assertFalse(outside.success)
            self.assertEqual(outside.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_pdf_missing_dependency_is_structured_when_pypdf2_unavailable(self):
        try:
            import PyPDF2  # noqa: F401
        except ImportError:
            pypdf2_available = False
        else:
            pypdf2_available = True
        if pypdf2_available:
            self.skipTest("PyPDF2 is available in this environment")

        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "sample.pdf").write_bytes(b"%PDF-1.4\n")

            result = _execute(workspace, {"path": "sample.pdf"})

            self.assertFalse(result.success)
            self.assertEqual(result.code, ToolErrorCode.DEPENDENCY_NOT_AVAILABLE.value)
            self.assertEqual(result.data["metadata"]["dependency"], "PyPDF2")


def _execute(workspace: str, args: dict):
    manager = ToolManager(workspace_root=workspace)
    return manager.execute(
        ToolCallRequest(
            tool_name="document_parser",
            args=args,
            context=ToolCallContext(workspace_root=workspace, source="test"),
            options=ToolCallOptions(allow_read_workspace=True),
        )
    )


def _write_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def _write_minimal_xlsx(path: Path) -> None:
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>Name</t></si><si><t>Value</t></si><si><t>Alpha</t></si>"
        "</sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>10</v></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


if __name__ == "__main__":
    unittest.main()

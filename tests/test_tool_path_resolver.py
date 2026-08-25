from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.tools.errors import ToolErrorCode
from src.tools.file_tools import PathResolver, ResolvedPath
from src.tools.path_policy import PathPolicy
from src.tools.policy import ToolPolicy
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.registry import ToolSpec


class ToolPathResolverTest(unittest.TestCase):
    def test_resolves_workspace_relative_file_and_serializes_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "src" / "app.py"
            target.parent.mkdir()
            target.write_text("print('ok')\n", encoding="utf-8")

            resolved = PathResolver(root).resolve("src/app.py")

            self.assertIsInstance(resolved, ResolvedPath)
            self.assertTrue(resolved.valid)
            self.assertTrue(resolved.exists)
            self.assertEqual(resolved.resource_type, "file")
            self.assertTrue(resolved.is_inside_workspace)
            self.assertEqual(resolved.workspace_relative_path, "src/app.py")
            self.assertFalse(resolved.is_sensitive)
            self.assertFalse(resolved.is_ignored)
            json.dumps(resolved.to_dict(), ensure_ascii=False)

    def test_allows_dot_dot_when_final_target_stays_inside_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = root / "docs"
            docs.mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")

            resolved = PathResolver(root).resolve("docs/../README.md")

            self.assertTrue(resolved.valid)
            self.assertTrue(resolved.is_inside_workspace)
            self.assertEqual(resolved.workspace_relative_path, "README.md")

    def test_blocks_dot_dot_and_absolute_paths_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            root.mkdir()
            outside = root.parent / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            resolver = PathResolver(root)

            escaped_relative = resolver.resolve("../outside.txt")
            escaped_absolute = resolver.resolve(outside)

            self.assertFalse(escaped_relative.is_inside_workspace)
            self.assertEqual(
                escaped_relative.error_code,
                ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
            )
            self.assertTrue(escaped_relative.is_blocked)
            self.assertFalse(escaped_absolute.is_inside_workspace)
            self.assertEqual(
                escaped_absolute.error_code,
                ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
            )

    def test_accepts_absolute_path_inside_workspace_and_windows_separators(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "nested" / "file.txt"
            target.parent.mkdir()
            target.write_text("hello", encoding="utf-8")
            resolver = PathResolver(root)

            absolute = resolver.resolve(target)
            with_backslash = resolver.resolve("nested\\file.txt")

            self.assertEqual(absolute.workspace_relative_path, "nested/file.txt")
            self.assertEqual(with_backslash.workspace_relative_path, "nested/file.txt")
            self.assertTrue(absolute.is_inside_workspace)
            self.assertTrue(with_backslash.is_inside_workspace)

    def test_rejects_empty_nul_and_non_path_values(self):
        resolver = PathResolver(".")

        empty = resolver.resolve("")
        nul = resolver.resolve("bad\x00path")
        non_path = resolver.resolve(123)  # type: ignore[arg-type]

        self.assertFalse(empty.valid)
        self.assertEqual(empty.error_code, ToolErrorCode.INVALID_ARGS.value)
        self.assertFalse(nul.valid)
        self.assertFalse(non_path.valid)

    def test_marks_sensitive_paths_and_ignored_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            (root / "cert.pem").write_text("secret", encoding="utf-8")
            (root / "credentials.local").write_text("secret", encoding="utf-8")
            node_modules = root / "node_modules"
            node_modules.mkdir()
            (node_modules / "pkg.json").write_text("{}", encoding="utf-8")
            resolver = PathResolver(root)

            env = resolver.resolve(".env")
            pem = resolver.resolve("cert.pem")
            credentials = resolver.resolve("credentials.local")
            ignored = resolver.resolve("node_modules/pkg.json")

            self.assertTrue(env.is_sensitive)
            self.assertTrue(env.is_blocked)
            self.assertTrue(pem.is_sensitive)
            self.assertTrue(credentials.is_sensitive)
            self.assertTrue(ignored.is_ignored)
            self.assertFalse(ignored.is_blocked)

    def test_configured_sensitive_blocked_and_ignored_paths_are_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resolver = PathResolver(
                root,
                sensitive_paths=["private.txt"],
                blocked_paths=["blocked"],
                ignored_paths=["generated"],
            )
            for relative in ("private.txt", "blocked/file.txt", "generated/out.txt"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x", encoding="utf-8")

            sensitive = resolver.resolve("private.txt")
            blocked = resolver.resolve("blocked/file.txt")
            ignored = resolver.resolve("generated/out.txt")

            self.assertTrue(sensitive.is_sensitive)
            self.assertEqual(
                sensitive.error_code,
                ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            )
            self.assertTrue(blocked.is_blocked)
            self.assertEqual(
                blocked.error_code,
                ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            )
            self.assertTrue(ignored.is_ignored)
            self.assertFalse(ignored.is_blocked)

    def test_path_policy_reuses_resolver_and_preserves_policy_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docs").mkdir()
            (root / "docs" / "readme.md").write_text("hello", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            policy = PathPolicy(root, ignored_paths=["docs"])

            ok = policy.check("docs/readme.md")
            sensitive = policy.check(".env")
            outside = policy.check("../outside.txt")

            self.assertFalse(ok.blocked)
            self.assertTrue(ok.ignored)
            self.assertEqual(ok.resource_type, "file")
            self.assertEqual(ok.affected_resource, "docs/readme.md")
            self.assertTrue(sensitive.blocked)
            self.assertEqual(sensitive.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)
            self.assertTrue(outside.blocked)
            self.assertEqual(outside.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)

    def test_tool_policy_uses_workspace_relative_resources_from_resolver(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request = ToolCallRequest(
                tool_name="reader",
                args={"path": "docs/readme.md"},
                context=ToolCallContext(workspace_root=root, source="test"),
                options=ToolCallOptions(allow_read_workspace=True),
            )
            decision = ToolPolicy().decide(
                ToolSpec(
                    name="reader",
                    description="Reader.",
                    workspace_scope="read_workspace",
                ),
                request,
            )

            self.assertTrue(decision.allowed)
            self.assertEqual(decision.affected_resources, ["docs/readme.md"])

    def test_symlink_escape_is_blocked_when_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            root.mkdir()
            outside = Path(temp_dir) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            link = root / "escape_link"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            resolved = PathResolver(root).resolve("escape_link")

            self.assertTrue(resolved.is_symlink)
            self.assertFalse(resolved.is_inside_workspace)
            self.assertEqual(
                resolved.error_code,
                ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
            )


if __name__ == "__main__":
    unittest.main()

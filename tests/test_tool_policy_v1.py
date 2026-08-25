from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.tools.errors import ToolErrorCode
from src.tools.path_policy import PathPolicy
from src.tools.policy import ToolPolicy, ToolPolicyDecision
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.registry import ToolSpec


class ToolPolicyV1Test(unittest.TestCase):
    def test_low_risk_tool_is_allowed_without_extra_capability(self):
        decision = _policy().decide(
            ToolSpec(name="calculator", description="Calculate."),
            _request(),
        )

        self.assertIsInstance(decision, ToolPolicyDecision)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.blocked)
        self.assertFalse(decision.requires_confirmation)
        self.assertEqual(decision.code, ToolErrorCode.OK.value)

    def test_blocked_risk_cannot_be_released_by_confirmation(self):
        spec = ToolSpec(
            name="blocked",
            description="Blocked.",
            risk_level="blocked",
            requires_confirmation=True,
        )
        request = _request(
            options=ToolCallOptions(
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash="preview-1",
            )
        )

        decision = _policy().decide(spec, request)

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.blocked)
        self.assertFalse(decision.requires_confirmation)
        self.assertEqual(decision.code, ToolErrorCode.BLOCKED_BY_POLICY.value)

    def test_high_risk_requires_a_confirmation_ticket(self):
        decision = _policy().decide(
            ToolSpec(name="high", description="High risk.", risk_level="high"),
            _request(),
        )

        self.assertFalse(decision.allowed)
        self.assertFalse(decision.blocked)
        self.assertTrue(decision.requires_confirmation)
        self.assertTrue(decision.preview_required)
        self.assertEqual(decision.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)

    def test_confirmed_must_come_from_options_not_args(self):
        spec = ToolSpec(name="high", description="High risk.", risk_level="high")
        model_claim = _request(args={"confirmed": True})

        rejected = _policy().decide(spec, model_claim)
        accepted = _policy().decide(
            spec,
            _request(
                args={"confirmed": True},
                options=ToolCallOptions(
                    confirmed=True,
                    confirmation_id="confirmation-1",
                    preview_hash="preview-1",
                ),
            ),
        )

        self.assertEqual(rejected.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
        self.assertFalse(rejected.allowed)
        self.assertTrue(accepted.allowed)
        self.assertEqual(accepted.code, ToolErrorCode.OK.value)

    def test_confirmation_preview_hash_must_match_when_expected_hash_is_given(self):
        spec = ToolSpec(name="high", description="High risk.", risk_level="high")
        request = _request(
            options=ToolCallOptions(
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash="old-preview",
            )
        )

        decision = _policy().decide(
            spec,
            request,
            expected_preview_hash="new-preview",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.code, ToolErrorCode.PREVIEW_CONFLICT.value)
        self.assertTrue(decision.preview_required)

    def test_write_confirmation_ticket_grants_one_call_when_session_capability_is_off(self):
        spec = ToolSpec(
            name="writer",
            description="Write.",
            risk_level="high",
            workspace_scope="write_workspace",
        )
        request = _request(
            options=ToolCallOptions(
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash="preview-1",
                allow_write_workspace=False,
            )
        )

        decision = _policy().decide(
            spec,
            request,
            resolved_paths=["output.txt"],
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, ToolErrorCode.OK.value)
        self.assertFalse(decision.requires_confirmation)

    def test_write_dry_run_preview_is_allowed_before_one_call_authorization(self):
        spec = ToolSpec(
            name="writer",
            description="Write.",
            risk_level="high",
            workspace_scope="write_workspace",
            supports_dry_run=True,
        )
        request = _request(
            options=ToolCallOptions(
                dry_run=True,
                allow_write_workspace=False,
            )
        )

        decision = _policy().decide(spec, request, resolved_paths=["output.txt"])

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_confirmation)
        self.assertTrue(decision.preview_required)

    def test_network_and_shell_capabilities_are_independent(self):
        search = ToolSpec(
            name="web_search",
            description="Search.",
            workspace_scope="network",
        )
        shell = ToolSpec(
            name="shell",
            description="Shell.",
            workspace_scope="shell_command",
        )

        network_decision = _policy().decide(search, _request())
        shell_decision = _policy().decide(
            shell,
            _request(
                options=ToolCallOptions(
                    allow_command=True,
                    allow_shell_command=False,
                )
            ),
        )

        self.assertEqual(network_decision.code, ToolErrorCode.NETWORK_NOT_ALLOWED.value)
        self.assertEqual(shell_decision.code, ToolErrorCode.COMMAND_BLOCKED.value)

    def test_workspace_boundary_has_priority_over_confirmation(self):
        with tempfile.TemporaryDirectory() as workspace:
            outside = str(Path(workspace).parent / "outside.txt")
            spec = ToolSpec(
                name="writer",
                description="Write.",
                risk_level="high",
                workspace_scope="write_workspace",
            )
            request = _request(
                workspace_root=workspace,
                options=ToolCallOptions(
                    allow_write_workspace=True,
                    confirmed=True,
                    confirmation_id="confirmation-1",
                    preview_hash="preview-1",
                ),
            )

            decision = _policy().decide(spec, request, resolved_paths=[outside])

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.code, ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value)
        self.assertFalse(decision.requires_confirmation)

    def test_sensitive_and_configured_blocked_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            spec = ToolSpec(
                name="reader",
                description="Read.",
                workspace_scope="read_workspace",
            )
            request = _request(
                workspace_root=workspace,
                options=ToolCallOptions(allow_read_workspace=True),
            )
            policy = ToolPolicy(
                path_policy=PathPolicy(
                    sensitive_paths=["private.txt"],
                    blocked_paths=["blocked.txt"],
                )
            )

            sensitive = policy.decide(spec, request, resolved_paths=["private.txt"])
            blocked = policy.decide(spec, request, resolved_paths=["blocked.txt"])

        self.assertEqual(sensitive.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)
        self.assertEqual(blocked.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)
        self.assertTrue(sensitive.blocked)
        self.assertTrue(blocked.blocked)

    def test_sensitive_read_can_require_confirmation_only_when_tool_declares_it(self):
        with tempfile.TemporaryDirectory() as workspace:
            regular_spec = ToolSpec(
                name="reader",
                description="Read.",
                workspace_scope="read_workspace",
            )
            sensitive_read_spec = ToolSpec(
                name="read_file",
                description="Read file.",
                workspace_scope="read_workspace",
                metadata={"allow_sensitive_read_with_confirmation": True},
            )
            request = _request(
                workspace_root=workspace,
                options=ToolCallOptions(allow_read_workspace=True),
            )
            confirmed_request = _request(
                workspace_root=workspace,
                options=ToolCallOptions(
                    allow_read_workspace=True,
                    confirmed=True,
                    confirmation_id="confirmation-1",
                    preview_hash="preview-1",
                ),
            )
            policy = ToolPolicy(
                path_policy=PathPolicy(sensitive_paths=["private.txt"])
            )

            regular = policy.decide(
                regular_spec,
                request,
                resolved_paths=["private.txt"],
            )
            pending = policy.decide(
                sensitive_read_spec,
                request,
                resolved_paths=["private.txt"],
            )
            confirmed = policy.decide(
                sensitive_read_spec,
                confirmed_request,
                resolved_paths=["private.txt"],
            )

        self.assertEqual(regular.code, ToolErrorCode.SENSITIVE_PATH_BLOCKED.value)
        self.assertTrue(regular.blocked)
        self.assertEqual(pending.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)
        self.assertEqual(pending.risk_level, "high")
        self.assertTrue(pending.requires_confirmation)
        self.assertTrue(confirmed.allowed)
        self.assertEqual(confirmed.code, ToolErrorCode.OK.value)

    def test_admin_permission_is_reserved_and_never_granted_by_confirmation(self):
        spec = ToolSpec(
            name="admin_tool",
            description="Needs admin.",
            risk_level="high",
            metadata={"requires_admin": True},
        )
        request = _request(
            options=ToolCallOptions(
                confirmed=True,
                confirmation_id="confirmation-1",
                preview_hash="preview-1",
            )
        )

        decision = _policy().decide(spec, request)

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.code, ToolErrorCode.ADMIN_PERMISSION_REQUIRED.value)

    def test_model_requested_low_risk_or_no_confirmation_cannot_lower_spec_policy(self):
        spec = ToolSpec(
            name="high",
            description="High risk.",
            risk_level="high",
            requires_confirmation=True,
        )

        decision = _policy().decide(
            spec,
            _request(options=ToolCallOptions(require_confirmation=False)),
            requested_risk_level="low",
            requested_requires_confirmation=False,
        )

        self.assertEqual(decision.risk_level, "high")
        self.assertEqual(decision.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)

    def test_metadata_risk_by_arg_can_raise_but_not_lower_effective_risk(self):
        spec = ToolSpec(
            name="write_file",
            description="Write.",
            risk_level="medium",
            workspace_scope="write_workspace",
            metadata={
                "risk_by_arg": {
                    "write_mode": {
                        "create": "medium",
                        "overwrite": "high",
                    }
                }
            },
        )

        create = _policy().decide(
            spec,
            _request(
                args={"path": "out.txt", "write_mode": "create"},
                options=ToolCallOptions(allow_write_workspace=True),
            ),
        )
        overwrite = _policy().decide(
            spec,
            _request(
                args={"path": "out.txt", "write_mode": "overwrite"},
                options=ToolCallOptions(allow_write_workspace=True),
            ),
        )

        self.assertTrue(create.allowed)
        self.assertEqual(create.risk_level, "medium")
        self.assertFalse(overwrite.allowed)
        self.assertEqual(overwrite.risk_level, "high")
        self.assertEqual(overwrite.code, ToolErrorCode.CONFIRMATION_REQUIRED.value)

    def test_affected_resources_use_workspace_relative_paths(self):
        with tempfile.TemporaryDirectory() as workspace:
            spec = ToolSpec(
                name="reader",
                description="Read.",
                workspace_scope="read_workspace",
            )
            decision = _policy().decide(
                spec,
                _request(workspace_root=workspace),
                resolved_paths=[str(Path(workspace) / "docs" / "readme.md")],
            )

        self.assertEqual(decision.affected_resources, ["docs/readme.md"])


def _policy() -> ToolPolicy:
    return ToolPolicy()


def _request(
    *,
    args: dict | None = None,
    options: ToolCallOptions | None = None,
    workspace_root: str = ".",
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name="test_tool",
        args=args or {},
        context=ToolCallContext(workspace_root=workspace_root, source="test"),
        options=options or ToolCallOptions(),
    )


if __name__ == "__main__":
    unittest.main()

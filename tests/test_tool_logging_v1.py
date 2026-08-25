from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode
from src.tools.policy import ToolPolicy
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.registry import ToolRegistry, ToolSpec
from src.tools.runtime import ToolRuntime
from src.tools.tool_logger import JsonlToolLogger


class ToolLoggingV1Test(unittest.TestCase):
    def test_jsonl_records_success_failure_dry_run_and_confirmation(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "tools.log"
            logger = JsonlToolLogger(log_path)
            runtime = ToolRuntime(
                registry=ToolRegistry(
                    [
                        ToolSpec(
                            name="safe",
                            description="Safe.",
                            supports_dry_run=True,
                        ),
                        ToolSpec(
                            name="high",
                            description="High.",
                            risk_level="high",
                        ),
                    ]
                ),
                handlers={
                    "safe": lambda **kwargs: ToolResult.ok(
                        data={"stdout": "very private stdout", "value": 1},
                        raw_output="raw-private-output",
                    ),
                    "high": lambda: "confirmed",
                },
                policy=ToolPolicy(),
                logger=logger,
            )

            success = runtime.execute(
                _request(
                    "safe",
                    {
                        "file_path": "notes.txt",
                        "content": "private file contents",
                        "api_key": "input-secret",
                    },
                )
            )
            failed_confirmation = runtime.execute(_request("high"))
            dry_run = runtime.execute(_request("high", dry_run=True))
            preview_hash = dry_run.metadata["output_control"]["preview_hash"]
            confirmed = runtime.execute(
                _request(
                    "high",
                    options=ToolCallOptions(
                        confirmed=True,
                        confirmation_id="confirmation-1",
                        preview_hash=preview_hash,
                    ),
                )
            )

            records = _read_records(log_path)

            self.assertTrue(success.success)
            self.assertFalse(failed_confirmation.success)
            self.assertTrue(dry_run.success)
            self.assertTrue(confirmed.success)
            self.assertEqual(len(records), 4)
            self.assertEqual(
                {record["code"] for record in records},
                {
                    ToolErrorCode.OK.value,
                    ToolErrorCode.CONFIRMATION_REQUIRED.value,
                    ToolErrorCode.DRY_RUN_PREVIEW.value,
                },
            )
            self.assertTrue(any(record["dry_run"] for record in records))
            confirmation_record = next(
                record
                for record in records
                if record["confirmation_id"] == "confirmation-1"
            )
            self.assertTrue(confirmation_record["confirmed"])
            self.assertEqual(confirmation_record["tool_name"], "high")
            self.assertIn("input_summary", records[0])
            self.assertIn("options_summary", records[0])
            self.assertIn("output_summary", records[0])
            self.assertIn("artifacts", records[0]["metadata"])

    def test_log_excludes_raw_output_and_sensitive_inputs(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "tools.log"
            logger = JsonlToolLogger(log_path)
            request = _request(
                "command_tool",
                {
                    "command": "curl -H 'Authorization: Bearer command-secret' https://example.invalid",
                    "query": "secret search phrase",
                    "content": "full file body must not be logged",
                    "authorization": "Bearer input-secret",
                    "nested": {"token": "nested-secret"},
                },
                options=ToolCallOptions(
                    allow_command=True,
                    confirmed=True,
                    confirmation_id="confirmation",
                    preview_hash="preview",
                ),
            )
            result = ToolResult.ok(
                data={
                    "stdout": "complete stdout must not be logged",
                    "body": "complete web body must not be logged",
                },
                message="token=message-secret",
                raw_output="complete raw output must not be logged",
                raw_output_truncated=True,
                metadata={
                    "output_control": {
                        "raw_output_hash": "a" * 64,
                        "artifact_ref": "artifact://tool-output/audit-ref",
                        "affected_resources": ["work/out.txt"],
                    }
                },
            )

            self.assertTrue(logger.record(request, result))
            text = log_path.read_text(encoding="utf-8")
            record = json.loads(text)

            for secret in (
                "command-secret",
                "secret search phrase",
                "full file body must not be logged",
                "input-secret",
                "nested-secret",
                "complete stdout must not be logged",
                "complete web body must not be logged",
                "message-secret",
                "complete raw output must not be logged",
            ):
                self.assertNotIn(secret, text)
            self.assertEqual(record["raw_output_hash"], "a" * 64)
            self.assertTrue(record["raw_output_truncated"])
            self.assertEqual(
                record["metadata"]["artifacts"],
                ["artifact://tool-output/audit-ref"],
            )
            self.assertEqual(record["input_summary"]["command"]["program"], "curl")
            self.assertEqual(
                record["input_summary"]["content"]["content"]["chars"],
                len("full file body must not be logged"),
            )

    def test_concurrent_records_remain_one_valid_json_object_per_line(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "tools.log"
            logger = JsonlToolLogger(log_path)

            def write_record(index: int) -> bool:
                return logger.record(
                    _request("concurrent", {"value": index}),
                    ToolResult.ok(data={"value": index}, call_id=f"call-{index}"),
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(write_record, range(64)))

            records = _read_records(log_path)
            self.assertTrue(all(results))
            self.assertEqual(logger.records_written, 64)
            self.assertEqual(len(records), 64)
            self.assertEqual({record["call_id"] for record in records}, {f"call-{index}" for index in range(64)})


def _request(
    tool_name: str,
    args: dict | None = None,
    *,
    dry_run: bool = False,
    options: ToolCallOptions | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=args or {},
        context=ToolCallContext(
            trace_id="trace-1",
            execution_id="execution-1",
            plan_id="plan-1",
            task_id="task-1",
            step_id="step-1",
            source="test",
        ),
        options=options or ToolCallOptions(dry_run=dry_run),
    )


def _read_records(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


if __name__ == "__main__":
    unittest.main()

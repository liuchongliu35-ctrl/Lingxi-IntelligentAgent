from __future__ import annotations

import json
import os
import sys
import time

PROTOCOL_VERSION = "2025-03-26"


def write_message(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(request: dict, result: object) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": result,
    }


def error_response(request: dict, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": {"code": code, "message": message},
    }


def main() -> int:
    stderr_text = os.getenv("FAKE_MCP_STDERR")
    if stderr_text:
        sys.stderr.write(stderr_text + "\n")
        sys.stderr.flush()

    initialized = False
    tools_list_calls = 0

    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            request = json.loads(text)
        except json.JSONDecodeError:
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
            continue
        method = request.get("method")
        params = request.get("params") or {}
        request_id = request.get("id")
        if method == "notifications/initialized":
            initialized = True
            continue
        if method == "initialize":
            write_message(response(request, initialize_result()))
        elif method == "tools/list":
            tools_list_calls += 1
            if not initialized:
                write_message(error_response(request, -32002, "initialized notification required"))
            elif os.getenv("FAKE_MCP_TOOLS_MODE") == "remote_error":
                write_message(error_response(request, -32003, "tools list failed"))
            elif os.getenv("FAKE_MCP_TOOLS_MODE") == "bad_result":
                write_message(response(request, {"tools": "not-a-list"}))
            else:
                write_message(response(request, {"tools": fake_tools(tools_list_calls)}))
        elif method == "tools/call":
            if not initialized:
                write_message(error_response(request, -32002, "initialized notification required"))
            else:
                write_message(response(request, fake_tools_call(params)))
        elif request_id is None:
            continue
        elif method == "echo":
            write_message(response(request, {"echo": params}))
        elif method == "env":
            names = params.get("names") or []
            write_message(
                response(
                    request,
                    {str(name): os.getenv(str(name)) for name in names},
                )
            )
        elif method == "notify_then_echo":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "method": "progress",
                    "params": {"message": "working"},
                }
            )
            write_message(response(request, {"ok": True}))
        elif method == "mismatch_then_echo":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": "stale",
                    "result": {"wrong": True},
                }
            )
            write_message(response(request, {"ok": True}))
        elif method == "invalid_json":
            sys.stdout.write("{not-json\n")
            sys.stdout.flush()
        elif method == "sleep":
            time.sleep(float(params.get("seconds", 1.0)))
            write_message(response(request, {"slept": True}))
        elif method == "exit_now":
            return int(params.get("code", 3))
        elif method == "remote_error":
            write_message(error_response(request, -32000, "remote failed"))
        else:
            write_message(response(request, {"method": method, "params": params}))
    return 0


def initialize_result() -> dict:
    mode = os.getenv("FAKE_MCP_INIT_MODE")
    if mode == "missing_protocol":
        return {
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fake-mcp", "version": "1.0"},
        }
    if mode == "unsupported_protocol":
        return {
            "protocolVersion": "1900-01-01",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fake-mcp", "version": "1.0"},
        }
    if mode == "bad_capabilities":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": "not-object",
            "serverInfo": {"name": "fake-mcp", "version": "1.0"},
        }
    if mode == "no_tools_capability":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "serverInfo": {"name": "fake-mcp", "version": "1.0"},
        }
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": True}},
        "serverInfo": {"name": "fake-mcp", "version": "1.0"},
    }


def fake_tools(call_count: int) -> list[dict]:
    schema_version = 2 if os.getenv("FAKE_MCP_REFRESH_CHANGES") and call_count > 1 else 1
    valid_tools = [
        {
            "name": "echo",
            "description": "Echo input through fake MCP.",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
        {
            "name": "search",
            "description": "Search fake records.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": schema_version},
                },
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "structured",
            "description": "Return structured content.",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
        {
            "name": "resource",
            "description": "Return a resource link.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "remote_error_tool",
            "description": "Return isError=true.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "invalid_result",
            "description": "Return an invalid tools/call result.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "big_text",
            "description": "Return large text content.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "sleep",
            "description": "Sleep before returning.",
            "inputSchema": {
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
            },
        },
        {
            "name": "exit_now",
            "description": "Exit the fake server without returning a tools/call response.",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "integer"}},
            },
        },
    ]
    if os.getenv("FAKE_MCP_TOOLS_MODE") == "invalid_schema":
        return valid_tools + [
            {
                "name": "broken_array",
                "description": "Invalid array schema.",
                "inputSchema": {"type": "array"},
            },
            {
                "name": "missing_schema",
                "description": "Missing schema.",
            },
            {
                "name": "",
                "description": "Missing name.",
                "inputSchema": {"type": "object"},
            },
        ]
    return valid_tools


def fake_tools_call(params: dict) -> dict:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name == "exit_now":
        raise SystemExit(int(arguments.get("code", 3)))
    if name == "sleep":
        time.sleep(float(arguments.get("seconds", 1.0)))
        return {"content": [{"type": "text", "text": "slept"}]}
    if name == "invalid_result":
        return {"content": "not-a-list"}
    if name == "remote_error_tool":
        return {
            "content": [{"type": "text", "text": "remote tool failed"}],
            "isError": True,
        }
    if name == "big_text":
        return {"content": [{"type": "text", "text": "x" * 1000}]}
    if name == "structured":
        return {
            "content": [{"type": "text", "text": "structured ok"}],
            "structuredContent": {"items": [arguments]},
        }
    if name == "resource":
        return {
            "content": [
                {"type": "text", "text": "resource ok"},
                {"type": "resource_link", "uri": "file:///tmp/fake.txt", "name": "fake.txt"},
            ],
        }
    return {
        "content": [{"type": "text", "text": f"called {name}"}],
        "structuredContent": {"name": name, "arguments": arguments},
    }


if __name__ == "__main__":
    raise SystemExit(main())

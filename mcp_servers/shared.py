"""Shared utilities for all Smart DevOps MCP servers."""

import json
import os
import subprocess
import sys
from typing import Any


def require_env(name: str) -> str:
    """Get a required environment variable or exit with a clear error."""
    value = os.environ.get(name)
    if not value:
        print(f"[ERROR] Required environment variable '{name}' is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def ok(data: Any) -> str:
    """Serialise a success payload to a JSON string."""
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)


def err(message: str, detail: Any = None) -> str:
    """Serialise an error response to a JSON string."""
    payload: dict[str, Any] = {"error": message}
    if detail is not None:
        payload["detail"] = detail
    return json.dumps(payload, indent=2, default=str)


def run_cmd(
    cmd: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def run_server(mcp_instance: Any, default_port: int) -> None:
    """
    Start an MCP server in stdio or SSE mode.

    MCP_TRANSPORT=stdio (default) — Claude Code subprocess model.
    MCP_TRANSPORT=sse             — HTTP daemon; connect via URL.

    SSE env vars:
      MCP_HOST — bind address (default: 127.0.0.1)
      MCP_PORT — port override (default: server-specific default_port)
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "sse":
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", str(default_port)))
        print(f"[MCP] Starting in SSE mode on http://{host}:{port}/sse", file=sys.stderr)
        mcp_instance.run(transport="sse", host=host, port=port)
    else:
        mcp_instance.run(transport="stdio")

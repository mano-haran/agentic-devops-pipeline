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
        print(
            f"[ERROR] Required environment variable '{name}' is not set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def optional_env(name: str, default: str = "") -> str:
    """Get an optional environment variable with a default."""
    return os.environ.get(name, default)


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


def http_headers_bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def http_headers_basic(username: str, token: str) -> dict[str, str]:
    import base64

    creds = base64.b64encode(f"{username}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

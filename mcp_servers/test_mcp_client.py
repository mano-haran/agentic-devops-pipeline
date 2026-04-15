#!/usr/bin/env python3
"""
Standalone MCP test client for Smart DevOps MCP servers.

Usage:
  python test_mcp_client.py <server_module> <tool_name> [key=value ...]

Examples:
  python test_mcp_client.py mcp_jira.server jira_get_issue issue_key=PROJ-123
  python test_mcp_client.py mcp_sonarqube.server sonar_get_quality_gate_status project_key=com.company:app branch=release/1.2.0
  python test_mcp_client.py mcp_jenkins.server jenkins_get_job_info job_name=smart-devops/build-app

The client reads environment variables from .env in the parent directory (if present).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Load .env if present
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


async def run_test(server_module: str, tool_name: str, arguments: dict) -> None:
    """Connect to an MCP server via stdio and invoke a single tool."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print("ERROR: 'mcp' package not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    server_params = StdioServerParameters(
        command="python",
        args=["-m", server_module],
        env=dict(os.environ),
    )

    print(f"\n{'='*60}")
    print(f"  Server : {server_module}")
    print(f"  Tool   : {tool_name}")
    print(f"  Args   : {json.dumps(arguments, indent=2)}")
    print(f"{'='*60}\n")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List available tools to verify the tool exists
            tools_result = await session.list_tools()
            available = [t.name for t in tools_result.tools]

            if tool_name not in available:
                print(f"ERROR: Tool '{tool_name}' not found in server '{server_module}'")
                print(f"Available tools:\n  " + "\n  ".join(sorted(available)))
                sys.exit(1)

            # Call the tool
            result = await session.call_tool(tool_name, arguments)

            for content in result.content:
                raw = getattr(content, "text", str(content))
                # Pretty-print JSON if possible
                try:
                    parsed = json.loads(raw)
                    print(json.dumps(parsed, indent=2))
                except (json.JSONDecodeError, TypeError):
                    print(raw)

            if result.isError:
                print("\n[TOOL RETURNED AN ERROR]", file=sys.stderr)
                sys.exit(1)
            else:
                print("\n[OK]")


async def list_tools_only(server_module: str) -> None:
    """List all tools in a server without calling any."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print("ERROR: 'mcp' package not installed.", file=sys.stderr)
        sys.exit(1)

    server_params = StdioServerParameters(
        command="python",
        args=["-m", server_module],
        env=dict(os.environ),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            print(f"\nTools in '{server_module}':\n")
            for tool in sorted(tools_result.tools, key=lambda t: t.name):
                print(f"  {tool.name}")
                if tool.description:
                    first_line = tool.description.strip().split("\n")[0]
                    print(f"    → {first_line}")
            print()


def parse_args(raw_args: list[str]) -> dict:
    """Parse key=value pairs from CLI args, supporting JSON values."""
    result = {}
    for arg in raw_args:
        if "=" not in arg:
            print(f"WARNING: Ignoring malformed argument '{arg}' (expected key=value)")
            continue
        key, _, value = arg.partition("=")
        key = key.strip()
        value = value.strip()
        # Try to parse as JSON for booleans, numbers, objects
        try:
            result[key] = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            result[key] = value
    return result


def print_usage() -> None:
    print(__doc__)
    print("\nAvailable server modules:")
    servers = [
        ("mcp_jira.server",      "Jira Data Center"),
        ("mcp_jenkins.server",   "Jenkins"),
        ("mcp_nexus.server",     "Nexus Repository Manager"),
        ("mcp_bitbucket.server", "Bitbucket Data Center"),
        ("mcp_sonarqube.server", "SonarQube"),
        ("mcp_coverity.server",  "Synopsys Coverity"),
        ("mcp_blackduck.server", "Synopsys Black Duck"),
    ]
    for module, desc in servers:
        print(f"  {module:<25} {desc}")
    print()
    print("To list all tools in a server:")
    print("  python test_mcp_client.py mcp_jira.server --list-tools")


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print_usage()
        sys.exit(0)

    if len(args) < 2:
        print_usage()
        sys.exit(1)

    server_module = args[0]
    tool_name_or_flag = args[1]

    if tool_name_or_flag == "--list-tools":
        asyncio.run(list_tools_only(server_module))
        return

    tool_name = tool_name_or_flag
    arguments = parse_args(args[2:])

    asyncio.run(run_test(server_module, tool_name, arguments))


if __name__ == "__main__":
    main()

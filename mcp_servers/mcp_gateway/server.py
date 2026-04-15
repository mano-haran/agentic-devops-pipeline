"""
MCP Gateway Server — single unified server for all Smart DevOps tools.

Aggregates all 7 MCP servers (Jira, Jenkins, Nexus, Bitbucket, SonarQube,
Coverity, Black Duck) into one FastMCP instance. This is the recommended
server to use in SSE mode — one port, one URL, all 47 tools.

Individual servers (mcp_jira, mcp_jenkins, etc.) continue to work
unchanged for Claude Code stdio mode.

Transport selection:
  stdio (default) — Claude Code subprocess; configure with "command" in settings.json
  sse             — HTTP daemon; configure with "url" in settings.json

Environment variables required:
  All credentials from the individual servers (see .env.example).
  MCP_TRANSPORT   — "stdio" or "sse" (default: "stdio")
  MCP_HOST        — bind address for SSE (default: 127.0.0.1)
  MCP_PORT        — port for SSE (default: 8000)
"""

import os
import sys

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import individual server modules.
# The @mcp.tool() decorator registers each function with its own local mcp
# instance AND returns the original function unchanged — so we can import and
# re-register those same functions with the gateway below.
import mcp_jira.server as _jira
import mcp_jenkins.server as _jenkins
import mcp_nexus.server as _nexus
import mcp_bitbucket.server as _bitbucket
import mcp_sonarqube.server as _sonarqube
import mcp_coverity.server as _coverity
import mcp_blackduck.server as _blackduck

from shared.utils import run_server

# ---------------------------------------------------------------------------
# Gateway instance
# ---------------------------------------------------------------------------

gateway = FastMCP(
    "smart-devops-gateway",
    instructions=(
        "Unified MCP gateway for the Smart DevOps CI/CD pipeline. "
        "Provides tools for Jira, Jenkins, Nexus, Bitbucket, SonarQube, "
        "Coverity, and Black Duck. All tool names are prefixed with their "
        "source system (e.g. jira_, jenkins_, nexus_)."
    ),
)

# ---------------------------------------------------------------------------
# Register tools from each server module.
#
# gateway.tool() returns a decorator; calling it with a function registers
# that function as a tool on the gateway instance. The function body, its
# docstring, and its type-annotated signature (which FastMCP uses to build
# the inputSchema) are all preserved exactly as defined in each server module.
# ---------------------------------------------------------------------------

# ── Jira ────────────────────────────────────────────────────────────────────
gateway.tool()(_jira.jira_get_issue)
gateway.tool()(_jira.jira_get_issue_status)
gateway.tool()(_jira.jira_get_transitions)
gateway.tool()(_jira.jira_transition_issue)
gateway.tool()(_jira.jira_update_issue)
gateway.tool()(_jira.jira_add_comment)
gateway.tool()(_jira.jira_create_issue)
gateway.tool()(_jira.jira_get_project_versions)

# ── Jenkins ─────────────────────────────────────────────────────────────────
gateway.tool()(_jenkins.jenkins_trigger_build)
gateway.tool()(_jenkins.jenkins_get_build_status)
gateway.tool()(_jenkins.jenkins_get_last_build)
gateway.tool()(_jenkins.jenkins_get_console_output)
gateway.tool()(_jenkins.jenkins_get_job_info)
gateway.tool()(_jenkins.jenkins_wait_for_build)

# ── Nexus ────────────────────────────────────────────────────────────────────
gateway.tool()(_nexus.nexus_upload_maven_artifact)
gateway.tool()(_nexus.nexus_upload_raw_artifact)
gateway.tool()(_nexus.nexus_upload_docker_image)
gateway.tool()(_nexus.nexus_download_artifact)
gateway.tool()(_nexus.nexus_search_artifacts)
gateway.tool()(_nexus.nexus_check_artifact_exists)
gateway.tool()(_nexus.nexus_list_repositories)

# ── Bitbucket ────────────────────────────────────────────────────────────────
gateway.tool()(_bitbucket.bitbucket_clone_repo)
gateway.tool()(_bitbucket.bitbucket_create_pr)
gateway.tool()(_bitbucket.bitbucket_get_pr)
gateway.tool()(_bitbucket.bitbucket_merge_pr)
gateway.tool()(_bitbucket.bitbucket_list_open_prs)
gateway.tool()(_bitbucket.bitbucket_create_tag)
gateway.tool()(_bitbucket.bitbucket_push_branch)
gateway.tool()(_bitbucket.bitbucket_get_pr_diff)
gateway.tool()(_bitbucket.bitbucket_get_commit_diff)

# ── SonarQube ────────────────────────────────────────────────────────────────
gateway.tool()(_sonarqube.sonar_get_quality_gate_status)
gateway.tool()(_sonarqube.sonar_get_metrics)
gateway.tool()(_sonarqube.sonar_get_issues)
gateway.tool()(_sonarqube.sonar_get_issue_suggestions)
gateway.tool()(_sonarqube.sonar_get_new_code_issues)
gateway.tool()(_sonarqube.sonar_get_project_analysis_status)
gateway.tool()(_sonarqube.sonar_list_projects)

# ── Coverity ─────────────────────────────────────────────────────────────────
gateway.tool()(_coverity.coverity_get_projects)
gateway.tool()(_coverity.coverity_get_streams)
gateway.tool()(_coverity.coverity_get_defects)
gateway.tool()(_coverity.coverity_get_defect_details)
gateway.tool()(_coverity.coverity_get_scan_summary)
gateway.tool()(_coverity.coverity_get_snapshots)

# ── Black Duck ───────────────────────────────────────────────────────────────
gateway.tool()(_blackduck.blackduck_list_projects)
gateway.tool()(_blackduck.blackduck_list_project_versions)
gateway.tool()(_blackduck.blackduck_get_vulnerabilities)
gateway.tool()(_blackduck.blackduck_get_vulnerability_details)
gateway.tool()(_blackduck.blackduck_get_policy_violations)
gateway.tool()(_blackduck.blackduck_get_components)
gateway.tool()(_blackduck.blackduck_get_scan_summary)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Initialise all sub-server credential globals before starting.
    # Each _init() reads its own env vars and sets module-level state
    # (BASE_URL, AUTH, HEADERS, etc.) that the tool functions close over.
    _jira._init()
    _jenkins._init()
    _nexus._init()
    _bitbucket._init()
    _sonarqube._init()
    _coverity._init()
    _blackduck._init()

    run_server(gateway, default_port=8000)


if __name__ == "__main__":
    main()

"""
MCP Gateway Server — single unified server for all Smart DevOps tools.

Aggregates all 7 MCP servers (Jira, Jenkins, Nexus, Bitbucket, SonarQube,
Coverity, Black Duck) into one FastMCP instance. Primary use case is SSE mode:
one port (8000), one URL, all 47 tools, with a single auth/authz layer.

Individual servers continue to work for Claude Code stdio mode. Use the gateway
when you need HTTP-based access, API key authentication, or audit logging.

Naming convention in the gateway:
  - Verb-first, no prefix for unique tool names: get_issue, trigger_build
  - Service prefix ONLY where names collide across servers:
      * list_projects  → sonarqube_list_projects / blackduck_list_projects
      * get_scan_summary → coverity_get_scan_summary / blackduck_get_scan_summary

Transport selection:
  MCP_TRANSPORT=stdio (default) — Claude Code subprocess mode
  MCP_TRANSPORT=sse             — HTTP daemon; configure with "url" in settings.json
  MCP_HOST — bind address (default: 127.0.0.1)
  MCP_PORT  — port (default: 8000)
"""

from mcp.server.fastmcp import FastMCP

import mcp_servers.jira as _jira
import mcp_servers.jenkins as _jenkins
import mcp_servers.nexus as _nexus
import mcp_servers.bitbucket as _bitbucket
import mcp_servers.sonarqube as _sonarqube
import mcp_servers.coverity as _coverity
import mcp_servers.blackduck as _blackduck

from mcp_servers.shared import run_server

# ---------------------------------------------------------------------------
# Gateway instance
# ---------------------------------------------------------------------------

gateway = FastMCP(
    "smart-devops-gateway",
    instructions=(
        "Unified MCP gateway for the Smart DevOps CI/CD pipeline. "
        "Provides tools for Jira, Jenkins, Nexus, Bitbucket, SonarQube, "
        "Coverity, and Black Duck. Tool names are verb-first (e.g. get_issue, "
        "trigger_build). Where names conflict across services, a service prefix "
        "is added (e.g. coverity_get_scan_summary, blackduck_get_scan_summary)."
    ),
)

# ---------------------------------------------------------------------------
# Register tools — verb-first names, service prefix only on conflicts.
# ---------------------------------------------------------------------------

# ── Jira ────────────────────────────────────────────────────────────────────
gateway.tool()(_jira.get_issue)
gateway.tool()(_jira.get_issue_status)
gateway.tool()(_jira.get_transitions)
gateway.tool()(_jira.transition_issue)
gateway.tool()(_jira.update_issue)
gateway.tool()(_jira.add_comment)
gateway.tool()(_jira.create_issue)
gateway.tool()(_jira.get_project_versions)

# ── Jenkins ─────────────────────────────────────────────────────────────────
gateway.tool()(_jenkins.trigger_build)
gateway.tool()(_jenkins.get_build_status)
gateway.tool()(_jenkins.get_last_build)
gateway.tool()(_jenkins.get_console_output)
gateway.tool()(_jenkins.get_job_info)
gateway.tool()(_jenkins.wait_for_build)

# ── Nexus ────────────────────────────────────────────────────────────────────
gateway.tool()(_nexus.upload_maven_artifact)
gateway.tool()(_nexus.upload_raw_artifact)
gateway.tool()(_nexus.upload_docker_image)
gateway.tool()(_nexus.download_artifact)
gateway.tool()(_nexus.search_artifacts)
gateway.tool()(_nexus.check_artifact_exists)
gateway.tool()(_nexus.list_repositories)

# ── Bitbucket ────────────────────────────────────────────────────────────────
gateway.tool()(_bitbucket.clone_repo)
gateway.tool()(_bitbucket.create_pr)
gateway.tool()(_bitbucket.get_pr)
gateway.tool()(_bitbucket.merge_pr)
gateway.tool()(_bitbucket.list_open_prs)
gateway.tool()(_bitbucket.create_tag)
gateway.tool()(_bitbucket.push_branch)
gateway.tool()(_bitbucket.get_pr_diff)
gateway.tool()(_bitbucket.get_commit_diff)

# ── SonarQube ────────────────────────────────────────────────────────────────
gateway.tool()(_sonarqube.get_quality_gate_status)
gateway.tool()(_sonarqube.get_metrics)
gateway.tool()(_sonarqube.get_issues)
gateway.tool()(_sonarqube.get_issue_suggestions)
gateway.tool()(_sonarqube.get_new_code_issues)
gateway.tool()(_sonarqube.get_project_analysis_status)
# Conflict: sonarqube.list_projects vs blackduck.list_projects → add prefix
gateway.tool(name="sonarqube_list_projects")(_sonarqube.list_projects)

# ── Coverity ─────────────────────────────────────────────────────────────────
gateway.tool()(_coverity.get_projects)
gateway.tool()(_coverity.get_streams)
gateway.tool()(_coverity.get_defects)
gateway.tool()(_coverity.get_defect_details)
gateway.tool()(_coverity.get_snapshots)
# Conflict: coverity.get_scan_summary vs blackduck.get_scan_summary → add prefix
gateway.tool(name="coverity_get_scan_summary")(_coverity.get_scan_summary)

# ── Black Duck ───────────────────────────────────────────────────────────────
# Conflict: blackduck.list_projects vs sonarqube.list_projects → add prefix
gateway.tool(name="blackduck_list_projects")(_blackduck.list_projects)
gateway.tool()(_blackduck.list_project_versions)
gateway.tool()(_blackduck.get_vulnerabilities)
gateway.tool()(_blackduck.get_vulnerability_details)
gateway.tool()(_blackduck.get_policy_violations)
gateway.tool()(_blackduck.get_components)
# Conflict: blackduck.get_scan_summary vs coverity.get_scan_summary → add prefix
gateway.tool(name="blackduck_get_scan_summary")(_blackduck.get_scan_summary)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Initialise all sub-server credential globals before starting.
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

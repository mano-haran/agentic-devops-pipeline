"""
MCP Server: SonarQube (on-prem, Community/Developer/Enterprise Edition)

Environment variables required:
  SONAR_BASE_URL  - e.g. https://sonarqube.company.com
  SONAR_API_TOKEN - SonarQube user or project token
"""

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import err, ok, require_env, run_server

mcp = FastMCP("sonarqube")

BASE_URL: str = ""
AUTH: tuple[str, str] = ("", "")

# Default metrics fetched when no specific list is requested
DEFAULT_METRICS = [
    "bugs",
    "vulnerabilities",
    "code_smells",
    "coverage",
    "duplicated_lines_density",
    "ncloc",
    "reliability_rating",
    "security_rating",
    "maintainability_rating",
    "sqale_index",
    "alert_status",
    "quality_gate_details",
    "new_bugs",
    "new_vulnerabilities",
    "new_code_smells",
    "new_coverage",
    "new_duplicated_lines_density",
]


def _init() -> None:
    global BASE_URL, AUTH
    BASE_URL = require_env("SONAR_BASE_URL").rstrip("/")
    # SonarQube token-based auth: token as username, blank password
    AUTH = (require_env("SONAR_API_TOKEN"), "")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_quality_gate_status(
    project_key: str,
    branch: str = "",
    pull_request: str = "",
) -> str:
    """
    Check the quality gate status for a SonarQube project.
    Returns the overall gate result (OK / ERROR / NONE) and per-condition breakdown.

    Args:
        project_key:  SonarQube project key, e.g. 'com.company:my-service'
        branch:       Branch name (for branch analysis, e.g. 'release/1.2.0')
        pull_request: Pull request ID (mutually exclusive with branch)
    """
    params: dict = {"projectKey": project_key}
    if branch:
        params["branch"] = branch
    if pull_request:
        params["pullRequest"] = pull_request

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/qualitygates/project_status",
            auth=AUTH,
            params=params,
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Failed to get quality gate for '{project_key}'", r.text)

    data = r.json().get("projectStatus", {})

    conditions = [
        {
            "metric": c["metricKey"],
            "status": c["status"],
            "actual_value": c.get("actualValue"),
            "error_threshold": c.get("errorThreshold"),
            "comparator": c.get("comparator"),
        }
        for c in data.get("conditions", [])
    ]

    failed = [c for c in conditions if c["status"] == "ERROR"]

    return ok({
        "project_key": project_key,
        "branch": branch or "default",
        "status": data.get("status"),           # OK / ERROR / NONE / WARN
        "passes_gate": data.get("status") == "OK",
        "ignored_conditions": data.get("ignoredConditions", False),
        "failed_conditions": failed,
        "all_conditions": conditions,
    })


@mcp.tool()
async def get_metrics(
    project_key: str,
    branch: str = "",
    metric_keys: str = "",
) -> str:
    """
    Retrieve quality metrics for a SonarQube project component.

    Args:
        project_key: SonarQube project key
        branch:      Branch name (optional)
        metric_keys: Comma-separated metric keys to retrieve.
                     Leave blank for the standard DevOps metric set:
                     bugs, vulnerabilities, code_smells, coverage,
                     duplicated_lines_density, ncloc, reliability_rating,
                     security_rating, maintainability_rating, alert_status
    """
    keys = [k.strip() for k in metric_keys.split(",") if k.strip()] or DEFAULT_METRICS

    params: dict = {
        "component": project_key,
        "metricKeys": ",".join(keys),
    }
    if branch:
        params["branch"] = branch

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/measures/component",
            auth=AUTH,
            params=params,
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Failed to get metrics for '{project_key}'", r.text)

    component = r.json().get("component", {})
    measures = {
        m["metric"]: m.get("value", m.get("periods", [{}])[0].get("value", "N/A"))
        for m in component.get("measures", [])
    }

    return ok({
        "project_key": project_key,
        "branch": branch or "default",
        "name": component.get("name"),
        "qualifier": component.get("qualifier"),
        "metrics": measures,
    })


@mcp.tool()
async def get_issues(
    project_key: str,
    branch: str = "",
    severities: str = "BLOCKER,CRITICAL,MAJOR",
    types: str = "BUG,VULNERABILITY,CODE_SMELL",
    statuses: str = "OPEN,CONFIRMED,REOPENED",
    page: int = 1,
    page_size: int = 50,
) -> str:
    """
    Retrieve code issues from SonarQube with filtering by severity and type.
    Used by Inspector agent to identify what needs to be fixed.

    Args:
        project_key: SonarQube project key
        branch:      Branch name (optional)
        severities:  Comma-separated: BLOCKER, CRITICAL, MAJOR, MINOR, INFO
        types:       Comma-separated: BUG, VULNERABILITY, CODE_SMELL
        statuses:    Comma-separated: OPEN, CONFIRMED, REOPENED, RESOLVED, CLOSED
        page:        Page number (default 1)
        page_size:   Results per page, max 500 (default 50)
    """
    params: dict = {
        "componentKeys": project_key,
        "severities": severities,
        "types": types,
        "statuses": statuses,
        "p": page,
        "ps": min(page_size, 500),
        "additionalFields": "comments,rules",
    }
    if branch:
        params["branch"] = branch

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/issues/search",
            auth=AUTH,
            params=params,
            timeout=60,
        )

    if r.status_code != 200:
        return err(f"Failed to get issues for '{project_key}'", r.text)

    data = r.json()
    issues = data.get("issues", [])

    return ok({
        "project_key": project_key,
        "branch": branch or "default",
        "total": data.get("total", len(issues)),
        "page": page,
        "page_size": page_size,
        "issues": [
            {
                "key": iss["key"],
                "rule": iss["rule"],
                "severity": iss["severity"],
                "type": iss["type"],
                "status": iss["status"],
                "component": iss["component"],
                "line": iss.get("line"),
                "message": iss["message"],
                "effort": iss.get("effort"),
                "debt": iss.get("debt"),
                "tags": iss.get("tags", []),
                "creation_date": iss.get("creationDate"),
                "update_date": iss.get("updateDate"),
            }
            for iss in issues
        ],
    })


@mcp.tool()
async def get_issue_suggestions(
    project_key: str,
    branch: str = "",
    severities: str = "BLOCKER,CRITICAL",
    types: str = "BUG,VULNERABILITY",
) -> str:
    """
    Get code issues with rule descriptions and remediation guidance.
    Used by Inspector agent to understand HOW to fix each issue.

    Args:
        project_key: SonarQube project key
        branch:      Branch name (optional)
        severities:  Filter by severity (default: BLOCKER, CRITICAL only)
        types:       Filter by type (default: BUG, VULNERABILITY only)
    """
    params: dict = {
        "componentKeys": project_key,
        "severities": severities,
        "types": types,
        "statuses": "OPEN,CONFIRMED,REOPENED",
        "ps": 50,
        "additionalFields": "rules,comments",
    }
    if branch:
        params["branch"] = branch

    async with httpx.AsyncClient(verify=False) as client:
        issues_r = await client.get(
            f"{BASE_URL}/api/issues/search",
            auth=AUTH,
            params=params,
            timeout=60,
        )
        if issues_r.status_code != 200:
            return err("Failed to get issues", issues_r.text)

        data = issues_r.json()
        issues = data.get("issues", [])
        rules_map = {r["key"]: r for r in data.get("rules", [])}

        enriched = []
        for iss in issues:
            rule_info = rules_map.get(iss["rule"], {})
            enriched.append({
                "key": iss["key"],
                "severity": iss["severity"],
                "type": iss["type"],
                "component": iss["component"].replace(f"{project_key}:", ""),
                "line": iss.get("line"),
                "message": iss["message"],
                "effort": iss.get("effort"),
                "rule": {
                    "key": iss["rule"],
                    "name": rule_info.get("name", ""),
                    "description": rule_info.get("htmlDesc", ""),
                    "remediation_function": rule_info.get("remFnType", ""),
                    "remediation_effort": rule_info.get("remFnBaseEffort", ""),
                },
            })

    return ok({
        "project_key": project_key,
        "branch": branch or "default",
        "total_critical_issues": len(enriched),
        "issues_with_guidance": enriched,
    })


@mcp.tool()
async def get_new_code_issues(
    project_key: str,
    branch: str = "",
    severities: str = "BLOCKER,CRITICAL,MAJOR",
) -> str:
    """
    Get issues introduced in the new code period (since last version / since merge).
    This is what determines quality gate failure for new code policies.

    Args:
        project_key: SonarQube project key
        branch:      Branch name (optional)
        severities:  Filter by severity
    """
    params: dict = {
        "componentKeys": project_key,
        "severities": severities,
        "statuses": "OPEN,CONFIRMED,REOPENED",
        "sinceLeakPeriod": "true",
        "ps": 100,
    }
    if branch:
        params["branch"] = branch

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/issues/search",
            auth=AUTH,
            params=params,
            timeout=60,
        )

    if r.status_code != 200:
        return err("Failed to get new code issues", r.text)

    data = r.json()
    issues = data.get("issues", [])

    by_severity: dict = {}
    for iss in issues:
        sev = iss["severity"]
        by_severity.setdefault(sev, []).append({
            "component": iss["component"].replace(f"{project_key}:", ""),
            "line": iss.get("line"),
            "message": iss["message"],
            "type": iss["type"],
        })

    return ok({
        "project_key": project_key,
        "branch": branch or "default",
        "total_new_issues": data.get("total", len(issues)),
        "by_severity": {
            sev: {"count": len(items), "items": items}
            for sev, items in by_severity.items()
        },
    })


@mcp.tool()
async def get_project_analysis_status(
    project_key: str,
    branch: str = "",
) -> str:
    """
    Check whether a SonarQube project has a recent completed analysis.
    Use this after triggering a scan to know when results are ready.

    Args:
        project_key: SonarQube project key
        branch:      Branch name (optional)
    """
    params: dict = {"project": project_key}
    if branch:
        params["branch"] = branch

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/ce/activity",
            auth=AUTH,
            params={**params, "ps": 1, "status": "SUCCESS,FAILED,CANCELED"},
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Failed to get analysis status for '{project_key}'", r.text)

    tasks = r.json().get("tasks", [])
    if not tasks:
        return ok({
            "project_key": project_key,
            "has_analysis": False,
            "message": "No completed analysis found.",
        })

    latest = tasks[0]
    return ok({
        "project_key": project_key,
        "has_analysis": True,
        "task_id": latest["id"],
        "status": latest["status"],
        "analysis_id": latest.get("analysisId"),
        "submitted_at": latest.get("submittedAt"),
        "started_at": latest.get("startedAt"),
        "executed_at": latest.get("executedAt"),
        "duration_ms": latest.get("executionTimeMs"),
        "warnings": latest.get("warnings", []),
    })


@mcp.tool()
async def list_projects(
    search: str = "",
    page_size: int = 50,
) -> str:
    """
    List SonarQube projects (components). Useful for verifying project keys.

    Args:
        search:    Optional name/key search filter
        page_size: Max results to return (default 50)
    """
    params: dict = {"ps": page_size, "qualifiers": "TRK"}
    if search:
        params["q"] = search

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/projects/search",
            auth=AUTH,
            params=params,
            timeout=30,
        )

    if r.status_code != 200:
        return err("Failed to list projects", r.text)

    components = r.json().get("components", [])
    return ok({
        "total": len(components),
        "projects": [
            {
                "key": c["key"],
                "name": c["name"],
                "qualifier": c["qualifier"],
                "last_analysis": c.get("lastAnalysisDate", "never"),
            }
            for c in components
        ],
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _init()
    run_server(mcp, default_port=8005)


if __name__ == "__main__":
    main()

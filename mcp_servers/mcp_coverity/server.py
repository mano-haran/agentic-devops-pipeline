"""
MCP Server: Synopsys Coverity Connect (on-prem)

Environment variables required:
  COVERITY_BASE_URL  - e.g. https://coverity.company.com
  COVERITY_USERNAME  - Coverity username
  COVERITY_API_TOKEN - Coverity API token (or password)

API reference: Coverity Connect REST API v2 (Coverity 2021.06+)
Older versions use SOAP; this server targets the REST API.
"""

import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.utils import err, ok, require_env, run_server

# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

mcp = FastMCP("coverity")

BASE_URL: str = ""
AUTH: tuple[str, str] = ("", "")

# Severity mappings (Coverity uses impact levels)
IMPACT_ORDER = {"High": 3, "Medium": 2, "Low": 1}

# Default blockers for pipeline gate
BLOCKING_IMPACTS = {"High"}


def _init() -> None:
    global BASE_URL, AUTH
    BASE_URL = require_env("COVERITY_BASE_URL").rstrip("/")
    AUTH = (require_env("COVERITY_USERNAME"), require_env("COVERITY_API_TOKEN"))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def coverity_get_projects() -> str:
    """
    List all Coverity projects accessible to the authenticated user.
    Use this to find project names before querying streams or defects.
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/v2/projects",
            auth=AUTH,
            headers={"Accept": "application/json"},
            timeout=30,
        )

    if r.status_code != 200:
        return err("Failed to list Coverity projects", r.text)

    data = r.json()
    projects = data if isinstance(data, list) else data.get("items", [])

    return ok(
        {
            "total": len(projects),
            "projects": [
                {
                    "id": p.get("id", p.get("projectId", {}).get("name")),
                    "name": p.get("name", p.get("projectKey", {}).get("name")),
                    "description": p.get("description", ""),
                }
                for p in projects
            ],
        }
    )


@mcp.tool()
async def coverity_get_streams(project_name: str = "") -> str:
    """
    List Coverity streams, optionally filtered by project name.
    Streams map to branches/configurations within a project.

    Args:
        project_name: Filter streams belonging to this project (optional)
    """
    params: dict = {}
    if project_name:
        params["projectId.name"] = project_name

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/v2/streams",
            auth=AUTH,
            headers={"Accept": "application/json"},
            params=params,
            timeout=30,
        )

    if r.status_code != 200:
        return err("Failed to list Coverity streams", r.text)

    data = r.json()
    streams = data if isinstance(data, list) else data.get("items", [])

    return ok(
        {
            "total": len(streams),
            "streams": [
                {
                    "name": s.get("id", {}).get("name", s.get("name", "")),
                    "language": s.get("language", ""),
                    "project": s.get("primaryProjectId", {}).get("name", ""),
                    "description": s.get("description", ""),
                }
                for s in streams
            ],
        }
    )


@mcp.tool()
async def coverity_get_defects(
    project_name: str,
    stream_name: str = "",
    impact: str = "",
    status: str = "New,Triaged,Various",
    checker: str = "",
    page_size: int = 100,
    offset: int = 0,
) -> str:
    """
    Retrieve defects (findings) from a Coverity project stream.
    Returns a summary with counts by severity and a detailed defect list.

    Args:
        project_name: Coverity project name
        stream_name:  Stream name to filter (optional; uses project's default stream)
        impact:       Filter by impact: 'High', 'Medium', 'Low' (optional)
        status:       Comma-separated statuses (default: New,Triaged,Various)
        checker:      Filter by specific checker name (optional)
        page_size:    Number of defects per page (default 100)
        offset:       Pagination offset (default 0)
    """
    params: dict = {
        "projectId.name": project_name,
        "retrievalMode": "bySnapshot",
        "limit": page_size,
        "offset": offset,
    }
    if stream_name:
        params["streamId.name"] = stream_name
    if impact:
        params["impact"] = impact
    if checker:
        params["checkerName"] = checker

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/v2/issues/search",
            auth=AUTH,
            headers={"Accept": "application/json"},
            params=params,
            timeout=60,
        )

    if r.status_code != 200:
        # Try legacy endpoint
        async with httpx.AsyncClient(verify=False) as client2:
            r = await client2.get(
                f"{BASE_URL}/api/v2/defects",
                auth=AUTH,
                headers={"Accept": "application/json"},
                params=params,
                timeout=60,
            )
        if r.status_code != 200:
            return err(f"Failed to get defects for project '{project_name}'", r.text)

    data = r.json()
    defects = data if isinstance(data, list) else data.get("items", data.get("defects", []))

    # Summary by impact
    by_impact: dict[str, int] = {}
    for d in defects:
        imp = d.get("impact", d.get("displayImpact", "Unknown"))
        by_impact[imp] = by_impact.get(imp, 0) + 1

    blocking = [
        d for d in defects
        if d.get("impact", d.get("displayImpact", "")) in BLOCKING_IMPACTS
    ]

    return ok(
        {
            "project_name": project_name,
            "stream_name": stream_name or "default",
            "total_defects": len(defects),
            "pipeline_blocking": len(blocking),
            "by_impact": by_impact,
            "defects": [
                {
                    "cid": d.get("mergeKey", d.get("cid", d.get("issueId", ""))),
                    "checker": d.get("checkerName", d.get("checker", "")),
                    "impact": d.get("impact", d.get("displayImpact", "")),
                    "status": d.get("action", d.get("status", "")),
                    "classification": d.get("classification", ""),
                    "file": d.get("mainEventFilePathname",
                                  d.get("filePathname", d.get("file", ""))),
                    "line": d.get("mainEventLineNumber",
                                  d.get("lineNumber", d.get("line", ""))),
                    "function": d.get("functionDisplayName",
                                      d.get("functionName", "")),
                    "category": d.get("checkerSubcategoryLongDescription",
                                      d.get("category", "")),
                    "first_detected": d.get("firstDetected", ""),
                }
                for d in defects
            ],
        }
    )


@mcp.tool()
async def coverity_get_defect_details(cid: str, project_name: str) -> str:
    """
    Get detailed information about a specific Coverity defect including
    the full event trace and remediation guidance.

    Args:
        cid:          Coverity defect CID (merge key), e.g. '12345'
        project_name: Project name where the defect was found
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/v2/issues/{cid}",
            auth=AUTH,
            headers={"Accept": "application/json"},
            params={"projectId.name": project_name},
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Failed to get defect details for CID {cid}", r.text)

    d = r.json()
    return ok(
        {
            "cid": cid,
            "checker": d.get("checkerName", ""),
            "impact": d.get("impact", d.get("displayImpact", "")),
            "classification": d.get("classification", ""),
            "status": d.get("action", ""),
            "file": d.get("mainEventFilePathname", ""),
            "line": d.get("mainEventLineNumber", ""),
            "function": d.get("functionDisplayName", ""),
            "description": d.get("checkerSubcategoryLongDescription", ""),
            "remediation": d.get("fixGuidance", d.get("cweUrl", "")),
            "cwe": d.get("cwe", ""),
            "events": [
                {
                    "event_number": e.get("eventNumber"),
                    "event_tag": e.get("eventTag"),
                    "event_description": e.get("eventDescription"),
                    "file": e.get("filePathname"),
                    "line": e.get("lineNumber"),
                }
                for e in d.get("events", [])
            ],
        }
    )


@mcp.tool()
async def coverity_get_scan_summary(project_name: str, stream_name: str = "") -> str:
    """
    Get a pipeline-ready scan summary: total defects, blocking count,
    severity breakdown, and a pass/fail verdict based on configured thresholds.

    Thresholds (blocking):
      - High impact defects > 0  → FAIL
      - Medium impact defects > 5 → FAIL

    Args:
        project_name: Coverity project name
        stream_name:  Coverity stream name (optional)
    """
    # Reuse get_defects internally
    params: dict = {"projectId.name": project_name}
    if stream_name:
        params["streamId.name"] = stream_name

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/v2/issues/search",
            auth=AUTH,
            headers={"Accept": "application/json"},
            params={**params, "limit": 500},
            timeout=60,
        )

    if r.status_code != 200:
        return err(f"Failed to get scan summary for '{project_name}'", r.text)

    data = r.json()
    defects = data if isinstance(data, list) else data.get("items", data.get("defects", []))

    by_impact: dict[str, int] = {}
    for d in defects:
        imp = d.get("impact", d.get("displayImpact", "Unknown"))
        by_impact[imp] = by_impact.get(imp, 0) + 1

    high_count = by_impact.get("High", 0)
    medium_count = by_impact.get("Medium", 0)

    gate_passed = high_count == 0 and medium_count <= 5

    verdict = "PASSED" if gate_passed else "FAILED"
    blocking_reasons = []
    if high_count > 0:
        blocking_reasons.append(f"{high_count} High impact defect(s) found (threshold: 0)")
    if medium_count > 5:
        blocking_reasons.append(f"{medium_count} Medium impact defect(s) found (threshold: 5)")

    return ok(
        {
            "project_name": project_name,
            "stream_name": stream_name or "default",
            "verdict": verdict,
            "gate_passed": gate_passed,
            "blocking_reasons": blocking_reasons,
            "defect_summary": {
                "total": len(defects),
                "by_impact": by_impact,
            },
            "thresholds": {
                "high_max": 0,
                "medium_max": 5,
                "low": "warn_only",
            },
        }
    )


@mcp.tool()
async def coverity_get_snapshots(
    project_name: str,
    stream_name: str,
    limit: int = 5,
) -> str:
    """
    List recent analysis snapshots for a Coverity stream.
    Each snapshot represents a completed Coverity scan run.

    Args:
        project_name: Coverity project name
        stream_name:  Stream name
        limit:        Number of recent snapshots to return (default 5)
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/api/v2/snapshots",
            auth=AUTH,
            headers={"Accept": "application/json"},
            params={
                "projectId.name": project_name,
                "streamId.name": stream_name,
                "limit": limit,
            },
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Failed to get snapshots for stream '{stream_name}'", r.text)

    data = r.json()
    snapshots = data if isinstance(data, list) else data.get("items", [])

    return ok(
        {
            "project_name": project_name,
            "stream_name": stream_name,
            "snapshots": [
                {
                    "id": s.get("id", {}).get("id", s.get("snapshotId", "")),
                    "date": s.get("dateCreated", ""),
                    "description": s.get("description", ""),
                    "version": s.get("analysisVersion", ""),
                    "lines_of_code": s.get("loc", ""),
                }
                for s in snapshots
            ],
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _init()
    run_server(mcp, default_port=8006)


if __name__ == "__main__":
    main()

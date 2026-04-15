"""
MCP Server: Synopsys Black Duck (on-prem)

Environment variables required:
  BLACKDUCK_BASE_URL  - e.g. https://blackduck.company.com
  BLACKDUCK_API_TOKEN - Black Duck API token (User > API Tokens)

Authentication: Black Duck uses token exchange — the API token is exchanged
for a short-lived bearer token on first use, then reused within the session.

API reference: Black Duck REST API v6 (Black Duck 2022.2+)
"""

import os
import sys
from functools import lru_cache

import httpx
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.utils import err, ok, require_env, run_server

# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

mcp = FastMCP("blackduck")

BASE_URL: str = ""
API_TOKEN: str = ""

# CVSS score thresholds for pipeline gating
CVSS_CRITICAL_THRESHOLD = 9.0
CVSS_HIGH_THRESHOLD = 7.0


def _init() -> None:
    global BASE_URL, API_TOKEN
    BASE_URL = require_env("BLACKDUCK_BASE_URL").rstrip("/")
    API_TOKEN = require_env("BLACKDUCK_API_TOKEN")


async def _get_bearer_token(client: httpx.AsyncClient) -> str:
    """Exchange API token for a bearer token."""
    r = await client.post(
        f"{BASE_URL}/api/tokens/authenticate",
        headers={
            "Authorization": f"token {API_TOKEN}",
            "Accept": "application/vnd.blackducksoftware.user-4+json",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Black Duck authentication failed: {r.text}")
    return r.json()["bearerToken"]


def _bd_headers(bearer: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/vnd.blackducksoftware.component-detail-5+json",
        "Content-Type": "application/json",
    }


async def _find_project(client: httpx.AsyncClient, bearer: str, project_name: str) -> dict | None:
    """Find a project by exact name match."""
    r = await client.get(
        f"{BASE_URL}/api/projects",
        headers={**_bd_headers(bearer), "Accept": "application/vnd.blackducksoftware.project-detail-4+json"},
        params={"q": f"name:{project_name}", "limit": 10},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    items = r.json().get("items", [])
    for item in items:
        if item.get("name") == project_name:
            return item
    return None


async def _find_version(
    client: httpx.AsyncClient,
    bearer: str,
    project_href: str,
    version_name: str,
) -> dict | None:
    """Find a project version by exact name match."""
    versions_url = f"{project_href}/versions"
    r = await client.get(
        versions_url,
        headers={**_bd_headers(bearer), "Accept": "application/vnd.blackducksoftware.project-detail-5+json"},
        params={"q": f"versionName:{version_name}", "limit": 10},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    items = r.json().get("items", [])
    for item in items:
        if item.get("versionName") == version_name:
            return item
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def blackduck_list_projects(search: str = "", limit: int = 50) -> str:
    """
    List Black Duck projects with optional name search filter.
    Use this to find exact project names and their metadata.

    Args:
        search: Optional search string to filter by project name
        limit:  Max results to return (default 50)
    """
    async with httpx.AsyncClient(verify=False) as client:
        bearer = await _get_bearer_token(client)
        params: dict = {"limit": limit}
        if search:
            params["q"] = f"name:{search}"

        r = await client.get(
            f"{BASE_URL}/api/projects",
            headers={**_bd_headers(bearer), "Accept": "application/vnd.blackducksoftware.project-detail-4+json"},
            params=params,
            timeout=30,
        )

    if r.status_code != 200:
        return err("Failed to list Black Duck projects", r.text)

    items = r.json().get("items", [])
    return ok(
        {
            "total": len(items),
            "projects": [
                {
                    "name": p["name"],
                    "description": p.get("description", ""),
                    "created_at": p.get("createdAt", ""),
                    "href": p.get("_meta", {}).get("href", ""),
                }
                for p in items
            ],
        }
    )


@mcp.tool()
async def blackduck_list_project_versions(project_name: str, limit: int = 20) -> str:
    """
    List versions (scans) for a Black Duck project.

    Args:
        project_name: Exact Black Duck project name
        limit:        Max versions to return (default 20)
    """
    async with httpx.AsyncClient(verify=False) as client:
        bearer = await _get_bearer_token(client)
        project = await _find_project(client, bearer, project_name)
        if not project:
            return err(f"Project '{project_name}' not found in Black Duck")

        project_href = project["_meta"]["href"]
        r = await client.get(
            f"{project_href}/versions",
            headers={**_bd_headers(bearer), "Accept": "application/vnd.blackducksoftware.project-detail-5+json"},
            params={"limit": limit, "sort": "createdAt DESC"},
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Failed to list versions for '{project_name}'", r.text)

    items = r.json().get("items", [])
    return ok(
        {
            "project": project_name,
            "total": len(items),
            "versions": [
                {
                    "name": v["versionName"],
                    "phase": v.get("phase", ""),
                    "distribution": v.get("distribution", ""),
                    "created_at": v.get("createdAt", ""),
                    "href": v.get("_meta", {}).get("href", ""),
                }
                for v in items
            ],
        }
    )


@mcp.tool()
async def blackduck_get_vulnerabilities(
    project_name: str,
    version_name: str,
    min_cvss_score: float = 0.0,
    remediated: bool = False,
    limit: int = 100,
) -> str:
    """
    Get vulnerable components for a Black Duck project version.
    Returns CVSS scores, vulnerability IDs, and affected component details.

    Args:
        project_name:    Exact Black Duck project name
        version_name:    Version name to query, e.g. '1.2.0'
        min_cvss_score:  Only return vulnerabilities at or above this CVSS score
                         (e.g. 7.0 for High+, 9.0 for Critical only)
        remediated:      Include already remediated vulnerabilities (default: False)
        limit:           Max results (default 100)
    """
    async with httpx.AsyncClient(verify=False) as client:
        bearer = await _get_bearer_token(client)

        project = await _find_project(client, bearer, project_name)
        if not project:
            return err(f"Project '{project_name}' not found")

        version = await _find_version(
            client, bearer, project["_meta"]["href"], version_name
        )
        if not version:
            return err(f"Version '{version_name}' not found in project '{project_name}'")

        version_href = version["_meta"]["href"]
        r = await client.get(
            f"{version_href}/vulnerable-bom-components",
            headers={**_bd_headers(bearer),
                     "Accept": "application/vnd.blackducksoftware.bill-of-materials-6+json"},
            params={"limit": limit},
            timeout=60,
        )

    if r.status_code != 200:
        return err(f"Failed to get vulnerabilities for '{project_name}/{version_name}'", r.text)

    items = r.json().get("items", [])

    vulns = []
    for item in items:
        for vuln in item.get("vulnerabilityWithRemediation", [{}]) if isinstance(
            item.get("vulnerabilityWithRemediation"), list
        ) else [item.get("vulnerabilityWithRemediation", {})]:
            cvss3 = vuln.get("overallScore", vuln.get("cvss3Score", 0.0)) or 0.0
            cvss2 = vuln.get("baseScore", 0.0) or 0.0
            score = max(float(cvss3), float(cvss2))

            if score < min_cvss_score:
                continue

            status = vuln.get("remediationStatus", "")
            if not remediated and status in ("REMEDIATED", "IGNORED", "PATCHED"):
                continue

            vulns.append(
                {
                    "vulnerability_name": vuln.get("vulnerabilityName", ""),
                    "cvss3_score": cvss3,
                    "cvss2_score": cvss2,
                    "severity": vuln.get("severity", vuln.get("baseScore", "")),
                    "component_name": item.get("componentName", ""),
                    "component_version": item.get("componentVersionName", ""),
                    "remediation_status": status,
                    "remediation_created_at": vuln.get("remediationCreatedAt", ""),
                    "description": vuln.get("description", ""),
                    "cwe_id": vuln.get("cweId", ""),
                    "published_date": vuln.get("publishedDate", ""),
                    "updated_date": vuln.get("updatedDate", ""),
                }
            )

    # Sort by CVSS score descending
    vulns.sort(key=lambda v: v["cvss3_score"], reverse=True)

    critical = [v for v in vulns if v["cvss3_score"] >= CVSS_CRITICAL_THRESHOLD]
    high = [v for v in vulns if CVSS_HIGH_THRESHOLD <= v["cvss3_score"] < CVSS_CRITICAL_THRESHOLD]
    gate_passed = len(critical) == 0 and len(high) == 0

    return ok(
        {
            "project_name": project_name,
            "version_name": version_name,
            "total_vulnerabilities": len(vulns),
            "pipeline_gate": {
                "passed": gate_passed,
                "critical_count": len(critical),
                "high_count": len(high),
                "thresholds": {
                    "critical": f"CVSS >= {CVSS_CRITICAL_THRESHOLD} → block",
                    "high": f"CVSS >= {CVSS_HIGH_THRESHOLD} → block",
                },
                "blocking_reason": (
                    None if gate_passed else
                    f"{len(critical)} critical and {len(high)} high severity vulnerabilities found"
                ),
            },
            "vulnerabilities": vulns,
        }
    )


@mcp.tool()
async def blackduck_get_vulnerability_details(
    project_name: str,
    version_name: str,
    vulnerability_name: str,
) -> str:
    """
    Get detailed information about a specific vulnerability including
    description, CVSS vector, CWE, and available fix version.

    Args:
        project_name:       Exact Black Duck project name
        version_name:       Version name
        vulnerability_name: CVE or BD-CVE identifier, e.g. 'CVE-2021-44228'
    """
    async with httpx.AsyncClient(verify=False) as client:
        bearer = await _get_bearer_token(client)

        # Get vulnerability details from the BD vulnerability database
        r = await client.get(
            f"{BASE_URL}/api/vulnerabilities/{vulnerability_name}",
            headers={**_bd_headers(bearer),
                     "Accept": "application/vnd.blackducksoftware.vulnerability-4+json"},
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Vulnerability '{vulnerability_name}' not found", r.text)

    v = r.json()
    return ok(
        {
            "name": v.get("name", vulnerability_name),
            "description": v.get("description", ""),
            "published_date": v.get("publishedDate", ""),
            "updated_date": v.get("updatedDate", ""),
            "cvss3": {
                "score": v.get("cvss3", {}).get("baseScore"),
                "vector": v.get("cvss3", {}).get("vector"),
                "severity": v.get("cvss3", {}).get("severity"),
            },
            "cvss2": {
                "score": v.get("cvss2", {}).get("baseScore"),
                "vector": v.get("cvss2", {}).get("vector"),
                "severity": v.get("cvss2", {}).get("severity"),
            },
            "cwe_id": v.get("cweId", ""),
            "solution": v.get("solution", ""),
            "workaround": v.get("workaround", ""),
            "references": [ref.get("url", "") for ref in v.get("references", [])],
            "source": v.get("source", ""),
        }
    )


@mcp.tool()
async def blackduck_get_policy_violations(
    project_name: str,
    version_name: str,
) -> str:
    """
    Get policy violations for a Black Duck project version.
    Policy violations indicate components that breach defined open-source policies.

    Args:
        project_name: Exact Black Duck project name
        version_name: Version name
    """
    async with httpx.AsyncClient(verify=False) as client:
        bearer = await _get_bearer_token(client)

        project = await _find_project(client, bearer, project_name)
        if not project:
            return err(f"Project '{project_name}' not found")

        version = await _find_version(
            client, bearer, project["_meta"]["href"], version_name
        )
        if not version:
            return err(f"Version '{version_name}' not found in project '{project_name}'")

        version_href = version["_meta"]["href"]

        # Get policy status summary
        policy_r = await client.get(
            f"{version_href}/policy-status",
            headers={**_bd_headers(bearer),
                     "Accept": "application/vnd.blackducksoftware.bill-of-materials-6+json"},
            timeout=30,
        )

        # Get component-level violations
        components_r = await client.get(
            f"{version_href}/components",
            headers={**_bd_headers(bearer),
                     "Accept": "application/vnd.blackducksoftware.bill-of-materials-6+json"},
            params={"filter": "policyStatus:IN_VIOLATION", "limit": 100},
            timeout=30,
        )

    policy_status = policy_r.json() if policy_r.status_code == 200 else {}
    components = components_r.json().get("items", []) if components_r.status_code == 200 else []

    return ok(
        {
            "project_name": project_name,
            "version_name": version_name,
            "overall_policy_status": policy_status.get("overallStatus", "UNKNOWN"),
            "in_violation": policy_status.get("overallStatus") == "IN_VIOLATION",
            "components_in_violation": len(components),
            "violated_components": [
                {
                    "component_name": c.get("componentName", ""),
                    "component_version": c.get("componentVersionName", ""),
                    "license": c.get("licenses", [{}])[0].get("licenseName", "") if c.get("licenses") else "",
                    "policy_status": c.get("policyStatus", ""),
                    "approval_status": c.get("approvalStatus", ""),
                    "violated_policy_names": [
                        p.get("policy", {}).get("name", "")
                        for p in c.get("policyViolations", [])
                    ],
                }
                for c in components
            ],
        }
    )


@mcp.tool()
async def blackduck_get_components(
    project_name: str,
    version_name: str,
    limit: int = 200,
) -> str:
    """
    Get the full Bill of Materials (BOM) — all open-source components
    detected in a Black Duck project version.

    Args:
        project_name: Exact Black Duck project name
        version_name: Version name
        limit:        Max components to return (default 200)
    """
    async with httpx.AsyncClient(verify=False) as client:
        bearer = await _get_bearer_token(client)

        project = await _find_project(client, bearer, project_name)
        if not project:
            return err(f"Project '{project_name}' not found")

        version = await _find_version(
            client, bearer, project["_meta"]["href"], version_name
        )
        if not version:
            return err(f"Version '{version_name}' not found in project '{project_name}'")

        version_href = version["_meta"]["href"]
        r = await client.get(
            f"{version_href}/components",
            headers={**_bd_headers(bearer),
                     "Accept": "application/vnd.blackducksoftware.bill-of-materials-6+json"},
            params={"limit": limit},
            timeout=60,
        )

    if r.status_code != 200:
        return err(f"Failed to get components for '{project_name}/{version_name}'", r.text)

    items = r.json().get("items", [])

    return ok(
        {
            "project_name": project_name,
            "version_name": version_name,
            "total_components": len(items),
            "components": [
                {
                    "name": c.get("componentName", ""),
                    "version": c.get("componentVersionName", ""),
                    "licenses": [lic.get("licenseName", "") for lic in c.get("licenses", [])],
                    "policy_status": c.get("policyStatus", "NOT_IN_VIOLATION"),
                    "review_status": c.get("reviewStatus", ""),
                    "usages": c.get("usages", []),
                    "match_types": c.get("matchTypes", []),
                }
                for c in items
            ],
        }
    )


@mcp.tool()
async def blackduck_get_scan_summary(
    project_name: str,
    version_name: str,
) -> str:
    """
    Get a pipeline-ready scan summary combining vulnerability counts,
    policy status, and a pass/fail verdict.

    Thresholds:
      - Critical vulnerabilities (CVSS >= 9.0) > 0 → FAIL
      - High vulnerabilities (CVSS >= 7.0) > 0    → FAIL
      - Policy violations (IN_VIOLATION)           → FAIL

    Args:
        project_name: Exact Black Duck project name
        version_name: Version name
    """
    async with httpx.AsyncClient(verify=False) as client:
        bearer = await _get_bearer_token(client)

        project = await _find_project(client, bearer, project_name)
        if not project:
            return err(f"Project '{project_name}' not found")

        version = await _find_version(
            client, bearer, project["_meta"]["href"], version_name
        )
        if not version:
            return err(f"Version '{version_name}' not found in project '{project_name}'")

        version_href = version["_meta"]["href"]

        vuln_r = await client.get(
            f"{version_href}/vulnerable-bom-components",
            headers={**_bd_headers(bearer),
                     "Accept": "application/vnd.blackducksoftware.bill-of-materials-6+json"},
            params={"limit": 500},
            timeout=60,
        )

        policy_r = await client.get(
            f"{version_href}/policy-status",
            headers={**_bd_headers(bearer),
                     "Accept": "application/vnd.blackducksoftware.bill-of-materials-6+json"},
            timeout=30,
        )

    vuln_items = vuln_r.json().get("items", []) if vuln_r.status_code == 200 else []
    policy_status = policy_r.json() if policy_r.status_code == 200 else {}

    critical = high = medium = low = 0
    for item in vuln_items:
        vwr = item.get("vulnerabilityWithRemediation", {})
        if isinstance(vwr, list):
            vwr = vwr[0] if vwr else {}
        score = float(vwr.get("overallScore", vwr.get("cvss3Score", 0)) or 0)
        status = vwr.get("remediationStatus", "")
        if status in ("REMEDIATED", "IGNORED", "PATCHED"):
            continue
        if score >= 9.0:
            critical += 1
        elif score >= 7.0:
            high += 1
        elif score >= 4.0:
            medium += 1
        else:
            low += 1

    policy_violated = policy_status.get("overallStatus") == "IN_VIOLATION"
    gate_passed = critical == 0 and high == 0 and not policy_violated

    blocking_reasons = []
    if critical > 0:
        blocking_reasons.append(f"{critical} Critical vulnerability(ies) (CVSS >= 9.0)")
    if high > 0:
        blocking_reasons.append(f"{high} High vulnerability(ies) (CVSS >= 7.0)")
    if policy_violated:
        blocking_reasons.append("Open-source policy violations detected")

    return ok(
        {
            "project_name": project_name,
            "version_name": version_name,
            "verdict": "PASSED" if gate_passed else "FAILED",
            "gate_passed": gate_passed,
            "blocking_reasons": blocking_reasons,
            "vulnerability_summary": {
                "critical": critical,
                "high": high,
                "medium": medium,
                "low": low,
                "total": critical + high + medium + low,
            },
            "policy_status": policy_status.get("overallStatus", "UNKNOWN"),
            "thresholds": {
                "critical_cvss": f">= {CVSS_CRITICAL_THRESHOLD}",
                "high_cvss": f">= {CVSS_HIGH_THRESHOLD}",
                "policy_violations": "block on IN_VIOLATION",
            },
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _init()
    run_server(mcp, default_port=8007)


if __name__ == "__main__":
    main()

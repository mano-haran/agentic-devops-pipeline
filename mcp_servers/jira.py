"""
MCP Server: Jira Data Center

Environment variables:
  JIRA_BASE_URL   - e.g. https://jira.company.com
  JIRA_API_TOKEN  - Personal Access Token
  JIRA_USERNAME   - Service account username
"""

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import err, ok, require_env, run_server

mcp = FastMCP("jira")

BASE_URL: str = ""
HEADERS: dict[str, str] = {}


def _init() -> None:
    global BASE_URL, HEADERS
    BASE_URL = require_env("JIRA_BASE_URL").rstrip("/")
    token = require_env("JIRA_API_TOKEN")
    HEADERS = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Atlassian-Token": "no-check",
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_issue(issue_key: str) -> str:
    """
    Retrieve full details of a Jira issue including status, assignee,
    description, labels, fix versions and custom fields.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(f"{BASE_URL}/rest/api/2/issue/{issue_key}",
                             headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return err(f"Failed to get issue {issue_key}", r.text)
        data = r.json()
        fields = data.get("fields", {})
        return ok({
            "key": data["key"],
            "id": data["id"],
            "summary": fields.get("summary"),
            "status": fields.get("status", {}).get("name"),
            "status_category": fields.get("status", {}).get("statusCategory", {}).get("name"),
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "reporter": (fields.get("reporter") or {}).get("displayName"),
            "priority": (fields.get("priority") or {}).get("name"),
            "issue_type": fields.get("issuetype", {}).get("name"),
            "description": fields.get("description"),
            "labels": fields.get("labels", []),
            "fix_versions": [v["name"] for v in fields.get("fixVersions", [])],
            "components": [c["name"] for c in fields.get("components", [])],
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "project": fields.get("project", {}).get("key"),
            "custom_fields": {
                k: v for k, v in fields.items()
                if k.startswith("customfield_") and v is not None
            },
        })


@mcp.tool()
async def get_issue_status(issue_key: str) -> str:
    """
    Get the current workflow status of a Jira issue.
    Lightweight alternative to get_issue when only status is needed.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}?fields=status,summary",
            headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return err(f"Failed to get status for {issue_key}", r.text)
        data = r.json()
        status = data["fields"]["status"]
        return ok({
            "key": issue_key,
            "summary": data["fields"].get("summary"),
            "status_name": status["name"],
            "status_id": status["id"],
            "status_category": status["statusCategory"]["name"],
        })


@mcp.tool()
async def get_transitions(issue_key: str) -> str:
    """
    List all workflow transitions available for a Jira issue in its current state.
    Use this to find the correct transition_id before calling transition_issue.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}/transitions",
            headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return err(f"Failed to get transitions for {issue_key}", r.text)
        transitions = [
            {"id": t["id"], "name": t["name"], "to_status": t["to"]["name"]}
            for t in r.json().get("transitions", [])
        ]
        return ok({"issue_key": issue_key, "transitions": transitions})


@mcp.tool()
async def transition_issue(
    issue_key: str,
    transition_id: str,
    comment: str = "",
    resolution: str = "",
) -> str:
    """
    Transition a Jira issue to a new workflow state.
    Use get_transitions first to find the correct transition_id.

    Args:
        issue_key:     Jira issue key, e.g. 'PROJ-123'
        transition_id: Numeric transition ID from get_transitions
        comment:       Optional comment to add when transitioning
        resolution:    Optional resolution name, e.g. 'Fixed', 'Done'
    """
    payload: dict = {"transition": {"id": transition_id}}
    if resolution:
        payload["fields"] = {"resolution": {"name": resolution}}
    if comment:
        payload["update"] = {"comment": [{"add": {"body": comment}}]}

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.post(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}/transitions",
            headers=HEADERS, json=payload, timeout=30)
        if r.status_code not in (200, 204):
            return err(f"Transition failed for {issue_key}", r.text)
        return ok({"success": True, "issue_key": issue_key, "transition_id": transition_id})


@mcp.tool()
async def update_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    labels: str = "",
    fix_version: str = "",
    assignee: str = "",
    custom_fields_json: str = "",
) -> str:
    """
    Update specific fields on a Jira issue. Only non-empty values are updated.

    Args:
        issue_key:          Jira issue key, e.g. 'PROJ-123'
        summary:            New summary text
        description:        New description text
        labels:             Comma-separated labels to SET (replaces existing)
        fix_version:        Fix version name, e.g. '1.2.0'
        assignee:           Assignee username
        custom_fields_json: JSON object of custom fields, e.g. '{"customfield_10100": "value"}'
    """
    fields: dict = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = description
    if labels:
        fields["labels"] = [lb.strip() for lb in labels.split(",") if lb.strip()]
    if fix_version:
        fields["fixVersions"] = [{"name": fix_version}]
    if assignee:
        fields["assignee"] = {"name": assignee}
    if custom_fields_json:
        try:
            fields.update(json.loads(custom_fields_json))
        except json.JSONDecodeError as exc:
            return err("custom_fields_json is not valid JSON", str(exc))
    if not fields:
        return err("No fields provided to update.")

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.put(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}",
            headers=HEADERS, json={"fields": fields}, timeout=30)
        if r.status_code not in (200, 204):
            return err(f"Update failed for {issue_key}", r.text)
        return ok({"success": True, "issue_key": issue_key, "updated_fields": list(fields.keys())})


@mcp.tool()
async def add_comment(issue_key: str, comment: str) -> str:
    """
    Add a comment to a Jira issue. Supports Jira wiki markup.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
        comment:   Comment body (supports Jira wiki markup)
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.post(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}/comment",
            headers=HEADERS, json={"body": comment}, timeout=30)
        if r.status_code not in (200, 201):
            return err(f"Failed to add comment to {issue_key}", r.text)
        data = r.json()
        return ok({
            "success": True,
            "comment_id": data.get("id"),
            "issue_key": issue_key,
            "author": data.get("author", {}).get("displayName"),
            "created": data.get("created"),
        })


@mcp.tool()
async def create_issue(
    project_key: str,
    issue_type: str,
    summary: str,
    description: str = "",
    parent_key: str = "",
    labels: str = "",
    fix_version: str = "",
    assignee: str = "",
    custom_fields_json: str = "",
) -> str:
    """
    Create a new Jira issue. Used by the pipeline to auto-raise
    UAT/PROD/DR deployment tickets on successful SIT deployment.

    Args:
        project_key:        Jira project key, e.g. 'PROJ'
        issue_type:         Issue type name, e.g. 'Story', 'Task'
        summary:            Issue summary
        description:        Issue description
        parent_key:         Parent issue key for sub-tasks
        labels:             Comma-separated label list
        fix_version:        Fix version name, e.g. '1.2.0'
        assignee:           Assignee username
        custom_fields_json: JSON object of custom field values
    """
    fields: dict = {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
        "summary": summary,
    }
    if description:
        fields["description"] = description
    if parent_key:
        fields["parent"] = {"key": parent_key}
    if labels:
        fields["labels"] = [lb.strip() for lb in labels.split(",") if lb.strip()]
    if fix_version:
        fields["fixVersions"] = [{"name": fix_version}]
    if assignee:
        fields["assignee"] = {"name": assignee}
    if custom_fields_json:
        try:
            fields.update(json.loads(custom_fields_json))
        except json.JSONDecodeError as exc:
            return err("custom_fields_json is not valid JSON", str(exc))

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.post(
            f"{BASE_URL}/rest/api/2/issue",
            headers=HEADERS, json={"fields": fields}, timeout=30)
        if r.status_code not in (200, 201):
            return err("Failed to create issue", r.text)
        data = r.json()
        return ok({
            "success": True,
            "issue_key": data["key"],
            "issue_id": data["id"],
            "url": f"{BASE_URL}/browse/{data['key']}",
        })


@mcp.tool()
async def get_project_versions(project_key: str) -> str:
    """
    List all versions defined in a Jira project.

    Args:
        project_key: Jira project key, e.g. 'PROJ'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/2/project/{project_key}/versions",
            headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return err(f"Failed to get versions for project {project_key}", r.text)
        return ok({
            "project": project_key,
            "versions": [
                {"id": v["id"], "name": v["name"],
                 "released": v.get("released", False),
                 "archived": v.get("archived", False)}
                for v in r.json()
            ],
        })


def main() -> None:
    _init()
    run_server(mcp, default_port=8001)


if __name__ == "__main__":
    main()

"""
MCP Server: Jira Data Center

Environment variables required:
  JIRA_BASE_URL   - e.g. https://jira.company.com
  JIRA_API_TOKEN  - Personal Access Token (PAT)
  JIRA_USERNAME   - Username associated with the PAT (for Basic auth fallback)
"""

import json
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

# Allow imports from parent directory when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.utils import err, ok, require_env

# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

mcp = FastMCP("jira-dc")

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
async def jira_get_issue(issue_key: str) -> str:
    """
    Retrieve full details of a Jira issue including status, assignee,
    description, labels, fix versions and custom fields.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}",
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            return err(f"Failed to get issue {issue_key}", r.text)

        data = r.json()
        fields = data.get("fields", {})
        return ok(
            {
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
                    k: v
                    for k, v in fields.items()
                    if k.startswith("customfield_") and v is not None
                },
            }
        )


@mcp.tool()
async def jira_get_issue_status(issue_key: str) -> str:
    """
    Get only the current workflow status and available transitions for a Jira issue.
    Lightweight alternative to jira_get_issue when only status is needed.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}?fields=status,summary",
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            return err(f"Failed to get status for {issue_key}", r.text)

        data = r.json()
        status = data["fields"]["status"]
        return ok(
            {
                "key": issue_key,
                "summary": data["fields"].get("summary"),
                "status_name": status["name"],
                "status_id": status["id"],
                "status_category": status["statusCategory"]["name"],
            }
        )


@mcp.tool()
async def jira_get_transitions(issue_key: str) -> str:
    """
    List all workflow transitions available for a Jira issue in its current state.
    Use this to find the correct transition_id before calling jira_transition_issue.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}/transitions",
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            return err(f"Failed to get transitions for {issue_key}", r.text)

        transitions = [
            {"id": t["id"], "name": t["name"], "to_status": t["to"]["name"]}
            for t in r.json().get("transitions", [])
        ]
        return ok({"issue_key": issue_key, "transitions": transitions})


@mcp.tool()
async def jira_transition_issue(
    issue_key: str,
    transition_id: str,
    comment: str = "",
    resolution: str = "",
) -> str:
    """
    Transition a Jira issue to a new workflow state.
    Use jira_get_transitions first to find the correct transition_id.

    Args:
        issue_key:     Jira issue key, e.g. 'PROJ-123'
        transition_id: Numeric transition ID (as string) from jira_get_transitions
        comment:       Optional comment to add when transitioning
        resolution:    Optional resolution name, e.g. 'Fixed', 'Done'
    """
    payload: dict = {"transition": {"id": transition_id}}

    if resolution:
        payload["fields"] = {"resolution": {"name": resolution}}
    if comment:
        payload["update"] = {
            "comment": [{"add": {"body": comment}}]
        }

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.post(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}/transitions",
            headers=HEADERS,
            json=payload,
            timeout=30,
        )
        if r.status_code not in (200, 204):
            return err(f"Transition failed for {issue_key}", r.text)

        return ok(
            {
                "success": True,
                "issue_key": issue_key,
                "transition_id": transition_id,
                "message": f"Issue {issue_key} successfully transitioned.",
            }
        )


@mcp.tool()
async def jira_update_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    labels: str = "",
    fix_version: str = "",
    assignee: str = "",
    custom_fields_json: str = "",
) -> str:
    """
    Update specific fields on a Jira issue.
    Only fields with non-empty values are updated.

    Args:
        issue_key:          Jira issue key, e.g. 'PROJ-123'
        summary:            New summary text (leave blank to skip)
        description:        New description text (leave blank to skip)
        labels:             Comma-separated labels to SET (replaces existing)
        fix_version:        Fix version name to set, e.g. '1.2.0'
        assignee:           Assignee username/accountId (leave blank to skip)
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
            custom = json.loads(custom_fields_json)
            fields.update(custom)
        except json.JSONDecodeError as exc:
            return err("custom_fields_json is not valid JSON", str(exc))

    if not fields:
        return err("No fields provided to update.")

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.put(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}",
            headers=HEADERS,
            json={"fields": fields},
            timeout=30,
        )
        if r.status_code not in (200, 204):
            return err(f"Update failed for {issue_key}", r.text)

        return ok({"success": True, "issue_key": issue_key, "updated_fields": list(fields.keys())})


@mcp.tool()
async def jira_add_comment(issue_key: str, comment: str) -> str:
    """
    Add a comment to a Jira issue. Supports Jira wiki markup.

    Args:
        issue_key: Jira issue key, e.g. 'PROJ-123'
        comment:   Comment body text (supports Jira wiki markup)
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.post(
            f"{BASE_URL}/rest/api/2/issue/{issue_key}/comment",
            headers=HEADERS,
            json={"body": comment},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            return err(f"Failed to add comment to {issue_key}", r.text)

        data = r.json()
        return ok(
            {
                "success": True,
                "comment_id": data.get("id"),
                "issue_key": issue_key,
                "author": data.get("author", {}).get("displayName"),
                "created": data.get("created"),
            }
        )


@mcp.tool()
async def jira_create_issue(
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
    Create a new Jira issue. Used by the pipeline to raise UAT/PROD/DR
    deployment tickets automatically on successful SIT deployment.

    Args:
        project_key:        Jira project key, e.g. 'PROJ'
        issue_type:         Issue type name, e.g. 'Story', 'Task', 'Sub-task'
        summary:            Issue summary
        description:        Issue description
        parent_key:         Parent issue key for sub-tasks, e.g. 'PROJ-123'
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
            headers=HEADERS,
            json={"fields": fields},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            return err("Failed to create issue", r.text)

        data = r.json()
        return ok(
            {
                "success": True,
                "issue_key": data["key"],
                "issue_id": data["id"],
                "url": f"{BASE_URL}/browse/{data['key']}",
            }
        )


@mcp.tool()
async def jira_get_project_versions(project_key: str) -> str:
    """
    List all versions defined in a Jira project. Useful for resolving
    fix version names when creating or updating issues.

    Args:
        project_key: Jira project key, e.g. 'PROJ'
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/2/project/{project_key}/versions",
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            return err(f"Failed to get versions for project {project_key}", r.text)

        versions = [
            {
                "id": v["id"],
                "name": v["name"],
                "released": v.get("released", False),
                "archived": v.get("archived", False),
            }
            for v in r.json()
        ]
        return ok({"project": project_key, "versions": versions})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _init()
    mcp.run()


if __name__ == "__main__":
    main()

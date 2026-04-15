"""
MCP Server: Bitbucket Data Center (on-prem)

Environment variables required:
  BITBUCKET_BASE_URL  - e.g. https://bitbucket.company.com
  BITBUCKET_API_TOKEN - Personal Access Token (HTTP Access Token in DC)
  BITBUCKET_USERNAME  - Username (used in clone URLs)

Note: Clone, push, and tag operations use the git CLI which must be configured
on the VM (SSH keys or credential helper). The BITBUCKET_BASE_URL and
BITBUCKET_API_TOKEN are used for REST API calls only.
"""

import os
import sys
import json
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.utils import err, ok, require_env, run_cmd

# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

mcp = FastMCP("bitbucket-dc")

BASE_URL: str = ""
HEADERS: dict[str, str] = {}
GIT_USERNAME: str = ""
GIT_TOKEN: str = ""


def _init() -> None:
    global BASE_URL, HEADERS, GIT_USERNAME, GIT_TOKEN
    BASE_URL = require_env("BITBUCKET_BASE_URL").rstrip("/")
    GIT_TOKEN = require_env("BITBUCKET_API_TOKEN")
    GIT_USERNAME = require_env("BITBUCKET_USERNAME")
    HEADERS = {
        "Authorization": f"Bearer {GIT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _clone_url(project_key: str, repo_slug: str) -> str:
    """Build an authenticated HTTPS clone URL for git CLI use."""
    parsed = urlparse(BASE_URL)
    return f"{parsed.scheme}://{GIT_USERNAME}:{GIT_TOKEN}@{parsed.netloc}/scm/{project_key.lower()}/{repo_slug}.git"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def bitbucket_clone_repo(
    project_key: str,
    repo_slug: str,
    target_dir: str,
    branch: str = "",
    depth: int = 0,
) -> str:
    """
    Clone a Bitbucket repository to the local filesystem using the git CLI.
    Credentials are embedded in the clone URL from environment variables.

    Args:
        project_key: Bitbucket project key, e.g. 'PROJ'
        repo_slug:   Repository slug, e.g. 'my-service'
        target_dir:  Local directory to clone into, e.g. '/tmp/builds/PROJ-123'
        branch:      Specific branch to clone (default: repo default branch)
        depth:       Shallow clone depth (0 = full history, 1 = fastest)
    """
    clone_url = _clone_url(project_key, repo_slug)

    cmd = ["git", "clone"]
    if branch:
        cmd += ["--branch", branch]
    if depth and depth > 0:
        cmd += ["--depth", str(depth)]
    cmd += [clone_url, target_dir]

    rc, stdout, stderr = run_cmd(cmd, timeout=300)

    # Mask the token in any logged output
    stderr_clean = stderr.replace(GIT_TOKEN, "***")
    stdout_clean = stdout.replace(GIT_TOKEN, "***")

    if rc != 0:
        return err("git clone failed", {"stderr": stderr_clean})

    return ok(
        {
            "success": True,
            "project_key": project_key,
            "repo_slug": repo_slug,
            "target_dir": target_dir,
            "branch": branch or "default",
            "output": stdout_clean,
        }
    )


@mcp.tool()
async def bitbucket_create_pr(
    project_key: str,
    repo_slug: str,
    title: str,
    description: str,
    source_branch: str,
    target_branch: str,
    reviewer_usernames: str = "",
) -> str:
    """
    Create a pull request in Bitbucket Data Center.

    Args:
        project_key:        Bitbucket project key, e.g. 'PROJ'
        repo_slug:          Repository slug, e.g. 'my-service'
        title:              PR title
        description:        PR description (supports Markdown)
        source_branch:      Source branch name, e.g. 'fix/sonar-PROJ-123'
        target_branch:      Target branch name, e.g. 'develop'
        reviewer_usernames: Comma-separated usernames to add as reviewers
    """
    reviewers = []
    if reviewer_usernames:
        reviewers = [
            {"user": {"name": u.strip()}}
            for u in reviewer_usernames.split(",")
            if u.strip()
        ]

    payload = {
        "title": title,
        "description": description,
        "state": "OPEN",
        "open": True,
        "closed": False,
        "fromRef": {
            "id": f"refs/heads/{source_branch}",
            "repository": {
                "slug": repo_slug,
                "project": {"key": project_key},
            },
        },
        "toRef": {
            "id": f"refs/heads/{target_branch}",
            "repository": {
                "slug": repo_slug,
                "project": {"key": project_key},
            },
        },
        "locked": False,
        "reviewers": reviewers,
    }

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.post(
            f"{BASE_URL}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}/pull-requests",
            headers=HEADERS,
            json=payload,
            timeout=30,
        )

    if r.status_code not in (200, 201):
        return err("Failed to create pull request", r.text)

    data = r.json()
    pr_url = next(
        (link["href"] for link in data.get("links", {}).get("self", []) if "href" in link),
        f"{BASE_URL}/projects/{project_key}/repos/{repo_slug}/pull-requests/{data['id']}",
    )

    return ok(
        {
            "success": True,
            "pr_id": data["id"],
            "pr_title": data["title"],
            "state": data["state"],
            "source_branch": source_branch,
            "target_branch": target_branch,
            "url": pr_url,
            "reviewers": [r["user"]["displayName"] for r in data.get("reviewers", [])],
        }
    )


@mcp.tool()
async def bitbucket_get_pr(
    project_key: str,
    repo_slug: str,
    pr_id: int,
) -> str:
    """
    Get full details of a Bitbucket pull request including status, reviewers,
    and merge conditions.

    Args:
        project_key: Bitbucket project key
        repo_slug:   Repository slug
        pr_id:       Pull request ID number
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}/pull-requests/{pr_id}",
            headers=HEADERS,
            timeout=30,
        )

    if r.status_code != 200:
        return err(f"Failed to get PR {pr_id}", r.text)

    data = r.json()
    pr_url = next(
        (link["href"] for link in data.get("links", {}).get("self", []) if "href" in link),
        "",
    )

    return ok(
        {
            "pr_id": data["id"],
            "title": data["title"],
            "description": data.get("description", ""),
            "state": data["state"],
            "open": data.get("open"),
            "closed": data.get("closed"),
            "merged": data.get("merged", False),
            "source_branch": data["fromRef"]["displayId"],
            "target_branch": data["toRef"]["displayId"],
            "author": data["author"]["user"]["displayName"],
            "created_date": data.get("createdDate"),
            "updated_date": data.get("updatedDate"),
            "reviewers": [
                {
                    "name": rv["user"]["displayName"],
                    "approved": rv.get("approved", False),
                    "status": rv.get("status", "UNAPPROVED"),
                }
                for rv in data.get("reviewers", [])
            ],
            "url": pr_url,
            "can_merge": data.get("properties", {}).get("mergeResult", {}).get("outcome") == "CLEAN",
        }
    )


@mcp.tool()
async def bitbucket_merge_pr(
    project_key: str,
    repo_slug: str,
    pr_id: int,
    merge_strategy: str = "merge-commit",
    message: str = "",
) -> str:
    """
    Merge an approved Bitbucket pull request.

    Args:
        project_key:    Bitbucket project key
        repo_slug:      Repository slug
        pr_id:          Pull request ID number
        merge_strategy: 'merge-commit', 'squash', or 'fast-forward'
                        (must match what the repo allows)
        message:        Custom merge commit message (optional)
    """
    # First get the PR version for optimistic locking
    async with httpx.AsyncClient(verify=False) as client:
        get_r = await client.get(
            f"{BASE_URL}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}/pull-requests/{pr_id}",
            headers=HEADERS,
            timeout=30,
        )
        if get_r.status_code != 200:
            return err(f"Failed to fetch PR {pr_id} before merge", get_r.text)

        pr_version = get_r.json().get("version", 0)

        payload: dict = {"version": pr_version}
        if message:
            payload["message"] = message

        r = await client.post(
            f"{BASE_URL}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}"
            f"/pull-requests/{pr_id}/merge?version={pr_version}",
            headers=HEADERS,
            json=payload,
            timeout=60,
        )

    if r.status_code not in (200, 201):
        return err(f"Failed to merge PR {pr_id}", r.text)

    data = r.json()
    return ok(
        {
            "success": True,
            "pr_id": pr_id,
            "state": data.get("state"),
            "merged": data.get("merged", False),
            "merge_strategy": merge_strategy,
        }
    )


@mcp.tool()
async def bitbucket_list_open_prs(
    project_key: str,
    repo_slug: str,
    target_branch: str = "",
    author_username: str = "",
    limit: int = 25,
) -> str:
    """
    List open pull requests in a Bitbucket repository with optional filters.

    Args:
        project_key:     Bitbucket project key
        repo_slug:       Repository slug
        target_branch:   Filter by target branch (optional)
        author_username: Filter by PR author username (optional)
        limit:           Max results to return (default 25)
    """
    params: dict = {"state": "OPEN", "limit": limit}
    if target_branch:
        params["at"] = f"refs/heads/{target_branch}"

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}/pull-requests",
            headers=HEADERS,
            params=params,
            timeout=30,
        )

    if r.status_code != 200:
        return err("Failed to list pull requests", r.text)

    prs = r.json().get("values", [])

    if author_username:
        prs = [
            pr for pr in prs
            if pr.get("author", {}).get("user", {}).get("name") == author_username
        ]

    return ok(
        {
            "total": len(prs),
            "pull_requests": [
                {
                    "id": pr["id"],
                    "title": pr["title"],
                    "state": pr["state"],
                    "source_branch": pr["fromRef"]["displayId"],
                    "target_branch": pr["toRef"]["displayId"],
                    "author": pr["author"]["user"]["displayName"],
                    "created_date": pr.get("createdDate"),
                }
                for pr in prs
            ],
        }
    )


@mcp.tool()
async def bitbucket_create_tag(
    repo_dir: str,
    tag_name: str,
    message: str,
    commit_sha: str = "",
    push: bool = True,
) -> str:
    """
    Create an annotated git tag in a local repository clone and optionally push it.
    Requires the git CLI and configured credentials on the VM.

    Args:
        repo_dir:   Absolute path to the local git repository
        tag_name:   Tag name to create, e.g. 'v1.2.0' or 'release-PROJ-123'
        message:    Annotated tag message
        commit_sha: Specific commit SHA to tag (default: HEAD)
        push:       Push the tag to origin after creating (default: True)
    """
    tag_cmd = ["git", "tag", "-a", tag_name, "-m", message]
    if commit_sha:
        tag_cmd.append(commit_sha)

    rc, stdout, stderr = run_cmd(tag_cmd, cwd=repo_dir)
    if rc != 0:
        return err(f"git tag failed", {"stderr": stderr})

    result = {"success": True, "tag_name": tag_name, "repo_dir": repo_dir}

    if push:
        push_cmd = ["git", "push", "origin", tag_name]
        rc, stdout, stderr = run_cmd(push_cmd, cwd=repo_dir)
        if rc != 0:
            return err(f"git push tag failed", {"stderr": stderr.replace(GIT_TOKEN, "***")})
        result["pushed"] = True
        result["push_output"] = stdout

    return ok(result)


@mcp.tool()
async def bitbucket_push_branch(
    repo_dir: str,
    branch_name: str,
    set_upstream: bool = True,
) -> str:
    """
    Push a local branch to the Bitbucket remote (origin).

    Args:
        repo_dir:     Absolute path to the local git repository
        branch_name:  Branch name to push
        set_upstream: Set the upstream tracking reference (--set-upstream)
    """
    cmd = ["git", "push"]
    if set_upstream:
        cmd += ["--set-upstream", "origin", branch_name]
    else:
        cmd += ["origin", branch_name]

    rc, stdout, stderr = run_cmd(cmd, cwd=repo_dir)

    if rc != 0:
        return err("git push failed", {"stderr": stderr.replace(GIT_TOKEN, "***")})

    return ok(
        {
            "success": True,
            "branch": branch_name,
            "repo_dir": repo_dir,
            "output": stdout,
        }
    )


@mcp.tool()
async def bitbucket_get_commit_diff(
    project_key: str,
    repo_slug: str,
    since_commit: str,
    until_commit: str = "HEAD",
    context_lines: int = 3,
) -> str:
    """
    Get the diff between two commits via the Bitbucket REST API.
    Useful for Inspector agent to understand what changed before code review.

    Args:
        project_key:   Bitbucket project key
        repo_slug:     Repository slug
        since_commit:  Starting commit SHA (exclusive)
        until_commit:  Ending commit SHA (inclusive, default HEAD)
        context_lines: Lines of context around each change (default 3)
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}/compare/diff",
            headers=HEADERS,
            params={
                "from": since_commit,
                "to": until_commit,
                "contextLines": context_lines,
                "whitespace": "IGNORE_ALL",
            },
            timeout=60,
        )

    if r.status_code != 200:
        return err("Failed to get commit diff", r.text)

    data = r.json()
    diffs = data.get("diffs", [])

    return ok(
        {
            "since": since_commit,
            "until": until_commit,
            "files_changed": len(diffs),
            "diffs": [
                {
                    "source": d.get("source", {}).get("toString", ""),
                    "destination": d.get("destination", {}).get("toString", ""),
                    "hunks": len(d.get("hunks", [])),
                }
                for d in diffs
            ],
        }
    )


@mcp.tool()
async def bitbucket_get_pr_diff(
    project_key: str,
    repo_slug: str,
    pr_id: int,
) -> str:
    """
    Get the file-level diff for a Bitbucket pull request.
    Used by Inspector agent for code review analysis.

    Args:
        project_key: Bitbucket project key
        repo_slug:   Repository slug
        pr_id:       Pull request ID
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}"
            f"/pull-requests/{pr_id}/diff",
            headers=HEADERS,
            timeout=60,
        )

    if r.status_code != 200:
        return err(f"Failed to get diff for PR {pr_id}", r.text)

    data = r.json()
    diffs = data.get("diffs", [])

    return ok(
        {
            "pr_id": pr_id,
            "files_changed": len(diffs),
            "diffs": [
                {
                    "path": d.get("destination", d.get("source", {})).get("toString", ""),
                    "type": "MODIFY" if d.get("source") and d.get("destination") else
                            "ADD" if d.get("destination") else "DELETE",
                    "hunks": [
                        {
                            "source_line": h["sourceLine"],
                            "dest_line": h["destinationLine"],
                            "segments": len(h.get("segments", [])),
                        }
                        for h in d.get("hunks", [])
                    ],
                }
                for d in diffs
            ],
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _init()
    mcp.run()


if __name__ == "__main__":
    main()

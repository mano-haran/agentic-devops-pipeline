"""
MCP Server: Jenkins (Data Center / on-prem)

Environment variables required:
  JENKINS_BASE_URL  - e.g. https://jenkins.company.com
  JENKINS_USERNAME  - Jenkins username
  JENKINS_API_TOKEN - Jenkins API token (User > Configure > API Token)
"""

import time

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import err, ok, require_env, run_server

mcp = FastMCP("jenkins")

BASE_URL: str = ""
AUTH: tuple[str, str] = ("", "")


def _init() -> None:
    global BASE_URL, AUTH
    BASE_URL = require_env("JENKINS_BASE_URL").rstrip("/")
    AUTH = (require_env("JENKINS_USERNAME"), require_env("JENKINS_API_TOKEN"))


async def _get_crumb(client: httpx.AsyncClient) -> dict[str, str]:
    """Fetch Jenkins CSRF crumb for POST requests."""
    r = await client.get(
        f"{BASE_URL}/crumbIssuer/api/json",
        auth=AUTH,
        timeout=15,
    )
    if r.status_code == 200:
        data = r.json()
        return {data["crumbRequestField"]: data["crumb"]}
    # Some Jenkins configs disable CSRF protection
    return {}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def trigger_build(
    job_name: str,
    parameters: str = "",
    wait_for_start: bool = True,
) -> str:
    """
    Trigger a Jenkins job build, optionally with parameters.
    Returns the queue item URL and build number once the build starts.

    Args:
        job_name:       Full job path, e.g. 'smart-devops/build-app'
                        or 'folder/subfolder/job-name'
        parameters:     Build parameters as comma-separated KEY=VALUE pairs, e.g.
                        'BRANCH=release/1.2.0,JIRA_TICKET=PROJ-123'
        wait_for_start: If true, poll the queue until the build number is assigned
                        (up to 60 seconds)
    """
    params: dict = {}
    if parameters:
        for pair in parameters.split(","):
            pair = pair.strip()
            if "=" in pair:
                key, _, value = pair.partition("=")
                params[key.strip()] = value.strip()
            else:
                return err(f"Invalid parameter format '{pair}': expected KEY=VALUE")

    # Encode job path (handles folders with slashes)
    job_path = "/job/".join(job_name.strip("/").split("/"))
    url_base = f"{BASE_URL}/job/{job_path}"

    async with httpx.AsyncClient(verify=False) as client:
        crumb = await _get_crumb(client)
        headers = {**crumb, "Content-Type": "application/x-www-form-urlencoded"}

        if params:
            r = await client.post(
                f"{url_base}/buildWithParameters",
                auth=AUTH,
                headers=headers,
                params=params,
                timeout=30,
            )
        else:
            r = await client.post(
                f"{url_base}/build",
                auth=AUTH,
                headers=headers,
                timeout=30,
            )

        if r.status_code not in (200, 201):
            return err(f"Failed to trigger build for '{job_name}'", r.text)

        queue_url = r.headers.get("Location", "")

        if not wait_for_start or not queue_url:
            return ok({"success": True, "job_name": job_name, "queue_url": queue_url})

        # Poll queue item to get the build number
        for _ in range(30):
            time.sleep(2)
            qr = await client.get(
                f"{queue_url}api/json",
                auth=AUTH,
                timeout=15,
            )
            if qr.status_code == 200:
                qdata = qr.json()
                executable = qdata.get("executable")
                if executable:
                    return ok({
                        "success": True,
                        "job_name": job_name,
                        "build_number": executable["number"],
                        "build_url": executable["url"],
                        "queue_url": queue_url,
                    })

        return ok({"success": True, "job_name": job_name, "queue_url": queue_url,
                   "note": "Build queued but number not yet assigned; check Jenkins UI."})


@mcp.tool()
async def get_build_status(job_name: str, build_number: int) -> str:
    """
    Get the current status and result of a specific Jenkins build.

    Args:
        job_name:     Full job path, e.g. 'smart-devops/build-app'
        build_number: Build number, e.g. 42
    """
    job_path = "/job/".join(job_name.strip("/").split("/"))
    url = f"{BASE_URL}/job/{job_path}/{build_number}/api/json"

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(url, auth=AUTH, timeout=30)
        if r.status_code != 200:
            return err(f"Failed to get build {build_number} for '{job_name}'", r.text)

        data = r.json()
        duration_s = data.get("duration", 0) / 1000
        return ok({
            "job_name": job_name,
            "build_number": data["number"],
            "result": data.get("result"),          # SUCCESS, FAILURE, ABORTED, null (in progress)
            "building": data.get("building", False),
            "duration_seconds": round(duration_s, 1),
            "timestamp": data.get("timestamp"),
            "url": data.get("url"),
            "display_name": data.get("displayName"),
            "description": data.get("description"),
            "causes": [
                c.get("shortDescription", "")
                for c in data.get("actions", [{}])[0].get("causes", [])
                if "shortDescription" in c
            ],
        })


@mcp.tool()
async def get_last_build(job_name: str) -> str:
    """
    Get status information for the last build of a Jenkins job.

    Args:
        job_name: Full job path, e.g. 'smart-devops/build-app'
    """
    job_path = "/job/".join(job_name.strip("/").split("/"))
    url = f"{BASE_URL}/job/{job_path}/lastBuild/api/json"

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(url, auth=AUTH, timeout=30)
        if r.status_code != 200:
            return err(f"Failed to get last build for '{job_name}'", r.text)

        data = r.json()
        return ok({
            "job_name": job_name,
            "build_number": data["number"],
            "result": data.get("result"),
            "building": data.get("building", False),
            "duration_seconds": round(data.get("duration", 0) / 1000, 1),
            "url": data.get("url"),
        })


@mcp.tool()
async def get_console_output(
    job_name: str,
    build_number: int,
    start_byte: int = 0,
) -> str:
    """
    Stream console output from a Jenkins build. Supports progressive fetching
    by tracking the byte offset; the response includes the next start_byte value.

    Args:
        job_name:     Full job path, e.g. 'smart-devops/build-app'
        build_number: Build number, e.g. 42
        start_byte:   Byte offset to begin reading from (0 for beginning).
                      Use the returned 'next_start_byte' on subsequent calls
                      to page through long logs.
    """
    job_path = "/job/".join(job_name.strip("/").split("/"))
    url = f"{BASE_URL}/job/{job_path}/{build_number}/logText/progressiveText"

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            url,
            auth=AUTH,
            params={"start": start_byte},
            timeout=60,
        )
        if r.status_code != 200:
            return err(f"Failed to get console output for build {build_number}", r.text)

        more = r.headers.get("X-More-Data", "false").lower() == "true"
        next_byte = int(r.headers.get("X-Text-Size", start_byte))

        return ok({
            "job_name": job_name,
            "build_number": build_number,
            "output": r.text,
            "more_data": more,
            "next_start_byte": next_byte,
        })


@mcp.tool()
async def get_job_info(job_name: str) -> str:
    """
    Get job configuration metadata including parameters, last build result,
    and build history summary.

    Args:
        job_name: Full job path, e.g. 'smart-devops/build-app'
    """
    job_path = "/job/".join(job_name.strip("/").split("/"))
    url = f"{BASE_URL}/job/{job_path}/api/json"

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(url, auth=AUTH, timeout=30)
        if r.status_code != 200:
            return err(f"Failed to get job info for '{job_name}'", r.text)

        data = r.json()
        return ok({
            "job_name": job_name,
            "display_name": data.get("displayName"),
            "description": data.get("description"),
            "buildable": data.get("buildable"),
            "url": data.get("url"),
            "last_build": {
                "number": (data.get("lastBuild") or {}).get("number"),
                "url": (data.get("lastBuild") or {}).get("url"),
            },
            "last_successful_build": {
                "number": (data.get("lastSuccessfulBuild") or {}).get("number"),
            },
            "last_failed_build": {
                "number": (data.get("lastFailedBuild") or {}).get("number"),
            },
            "health_report": [
                h.get("description") for h in data.get("healthReport", [])
            ],
            "parameters": [
                {
                    "name": p["defaultParameterValue"].get("name", ""),
                    "type": p["type"],
                    "default": p["defaultParameterValue"].get("value", ""),
                    "description": p.get("description", ""),
                }
                for action in data.get("actions", [])
                for p in action.get("parameterDefinitions", [])
            ],
        })


@mcp.tool()
async def wait_for_build(
    job_name: str,
    build_number: int,
    poll_interval_seconds: int = 15,
    timeout_seconds: int = 1800,
) -> str:
    """
    Poll a Jenkins build until it completes or the timeout is reached.
    Returns the final build result.

    Args:
        job_name:              Full job path
        build_number:          Build number to poll
        poll_interval_seconds: How often to check (default: 15s)
        timeout_seconds:       Give up after this many seconds (default: 30min)
    """
    job_path = "/job/".join(job_name.strip("/").split("/"))
    url = f"{BASE_URL}/job/{job_path}/{build_number}/api/json"
    elapsed = 0

    async with httpx.AsyncClient(verify=False) as client:
        while elapsed < timeout_seconds:
            r = await client.get(url, auth=AUTH, timeout=30)
            if r.status_code != 200:
                return err(f"Polling failed for build {build_number}", r.text)

            data = r.json()
            if not data.get("building", True):
                return ok({
                    "job_name": job_name,
                    "build_number": build_number,
                    "result": data.get("result"),
                    "duration_seconds": round(data.get("duration", 0) / 1000, 1),
                    "url": data.get("url"),
                    "elapsed_seconds": elapsed,
                })

            time.sleep(poll_interval_seconds)
            elapsed += poll_interval_seconds

    return err(
        f"Timeout after {timeout_seconds}s waiting for build {build_number}",
        {"job_name": job_name, "build_number": build_number},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _init()
    run_server(mcp, default_port=8002)


if __name__ == "__main__":
    main()

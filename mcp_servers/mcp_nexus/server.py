"""
MCP Server: Nexus Repository Manager 3 (on-prem)

Environment variables required:
  NEXUS_BASE_URL   - e.g. https://nexus.company.com
  NEXUS_USERNAME   - Nexus username
  NEXUS_PASSWORD   - Nexus password or API token
  NEXUS_DOCKER_HOST     - Docker registry hostname:port, e.g. nexus.company.com:5000
  NEXUS_DOCKER_REPO     - Docker hosted repo name, e.g. docker-hosted
"""

import os
import sys
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.utils import err, ok, require_env, run_cmd, run_server

# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

mcp = FastMCP("nexus")

BASE_URL: str = ""
AUTH: tuple[str, str] = ("", "")
DOCKER_HOST: str = ""
DOCKER_REPO: str = ""


def _init() -> None:
    global BASE_URL, AUTH, DOCKER_HOST, DOCKER_REPO
    BASE_URL = require_env("NEXUS_BASE_URL").rstrip("/")
    AUTH = (require_env("NEXUS_USERNAME"), require_env("NEXUS_PASSWORD"))
    DOCKER_HOST = os.environ.get("NEXUS_DOCKER_HOST", "")
    DOCKER_REPO = os.environ.get("NEXUS_DOCKER_REPO", "docker-hosted")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def nexus_upload_maven_artifact(
    repository: str,
    group_id: str,
    artifact_id: str,
    version: str,
    file_path: str,
    packaging: str = "jar",
    classifier: str = "",
) -> str:
    """
    Upload a Maven artifact (JAR, WAR, POM) to a Nexus Maven hosted repository.

    Args:
        repository:   Nexus hosted Maven repository name, e.g. 'maven-releases'
        group_id:     Maven group ID, e.g. 'com.company.app'
        artifact_id:  Maven artifact ID, e.g. 'my-service'
        version:      Artifact version, e.g. '1.2.0'
        file_path:    Absolute path to the artifact file on the VM
        packaging:    Artifact type: 'jar', 'war', 'pom' (default: 'jar')
        classifier:   Optional classifier, e.g. 'sources', 'javadoc'
    """
    artifact_path = Path(file_path)
    if not artifact_path.exists():
        return err(f"Artifact file not found: {file_path}")

    # Nexus 3 REST API v1 component upload
    url = f"{BASE_URL}/service/rest/v1/components?repository={repository}"

    classifier_suffix = f"-{classifier}" if classifier else ""
    filename = f"{artifact_id}-{version}{classifier_suffix}.{packaging}"

    with artifact_path.open("rb") as fh:
        files = {
            "maven2.groupId": (None, group_id),
            "maven2.artifactId": (None, artifact_id),
            "maven2.version": (None, version),
            f"maven2.asset1": (filename, fh, f"application/java-archive"),
            "maven2.asset1.extension": (None, packaging),
        }
        if classifier:
            files["maven2.asset1.classifier"] = (None, classifier)

        async with httpx.AsyncClient(verify=False) as client:
            r = await client.post(
                url,
                auth=AUTH,
                files=files,
                timeout=300,
            )

    if r.status_code not in (200, 204):
        return err("Maven artifact upload failed", r.text)

    return ok(
        {
            "success": True,
            "repository": repository,
            "group_id": group_id,
            "artifact_id": artifact_id,
            "version": version,
            "packaging": packaging,
            "classifier": classifier,
            "filename": filename,
        }
    )


@mcp.tool()
async def nexus_upload_raw_artifact(
    repository: str,
    directory: str,
    file_path: str,
    destination_filename: str = "",
) -> str:
    """
    Upload any file to a Nexus raw (hosted) repository.
    Useful for build reports, Helm charts, or arbitrary binaries.

    Args:
        repository:           Nexus raw repository name, e.g. 'raw-hosted'
        directory:            Target directory path in the repo, e.g. '/builds/1.2.0/'
        file_path:            Absolute path to the file on the VM
        destination_filename: Override filename in Nexus (default: same as source)
    """
    artifact_path = Path(file_path)
    if not artifact_path.exists():
        return err(f"File not found: {file_path}")

    dest_name = destination_filename or artifact_path.name
    dir_clean = directory.strip("/")

    url = f"{BASE_URL}/service/rest/v1/components?repository={repository}"

    with artifact_path.open("rb") as fh:
        files = {
            "raw.directory": (None, f"/{dir_clean}/"),
            "raw.asset1": (dest_name, fh, "application/octet-stream"),
            "raw.asset1.filename": (None, dest_name),
        }

        async with httpx.AsyncClient(verify=False) as client:
            r = await client.post(url, auth=AUTH, files=files, timeout=300)

    if r.status_code not in (200, 204):
        return err("Raw artifact upload failed", r.text)

    return ok(
        {
            "success": True,
            "repository": repository,
            "path": f"/{dir_clean}/{dest_name}",
            "url": f"{BASE_URL}/repository/{repository}/{dir_clean}/{dest_name}",
        }
    )


@mcp.tool()
async def nexus_upload_docker_image(
    local_image: str,
    image_tag: str,
    nexus_repo_path: str = "",
) -> str:
    """
    Tag and push a locally built Docker image to the Nexus Docker registry.
    Requires Docker CLI to be available on the VM.

    NEXUS_DOCKER_HOST env var must be set (e.g. nexus.company.com:5000).

    Args:
        local_image:    Local image name:tag, e.g. 'myapp:latest' or 'myapp:1.2.0'
        image_tag:      Tag to use in Nexus, e.g. '1.2.0-PROJ-123'
        nexus_repo_path: Override the image path in Nexus registry,
                         e.g. 'myorg/myapp'. Defaults to the local image name.
    """
    if not DOCKER_HOST:
        return err("NEXUS_DOCKER_HOST environment variable is not set.")

    base_name = nexus_repo_path or local_image.split(":")[0]
    remote_image = f"{DOCKER_HOST}/{base_name}:{image_tag}"

    # Step 1: tag
    rc, stdout, stderr = run_cmd(["docker", "tag", local_image, remote_image])
    if rc != 0:
        return err(f"docker tag failed", {"stdout": stdout, "stderr": stderr})

    # Step 2: login using env creds (password via stdin to avoid shell history)
    rc, stdout, stderr = run_cmd(
        ["docker", "login", DOCKER_HOST,
         "--username", AUTH[0], "--password-stdin"],
        env={"DOCKER_PASS": AUTH[1]},
    )
    # Note: docker login --password-stdin reads from stdin; we pipe via env trick.
    # Alternative: use DOCKER_CONFIG or pre-configured credential helper.
    if rc != 0:
        # Try without stdin (pre-configured credentials)
        rc, stdout, stderr = run_cmd(
            ["docker", "login", DOCKER_HOST, "-u", AUTH[0], "-p", AUTH[1]]
        )
        if rc != 0:
            return err("docker login failed", {"stderr": stderr})

    # Step 3: push
    rc, stdout, stderr = run_cmd(["docker", "push", remote_image], timeout=600)
    if rc != 0:
        return err("docker push failed", {"stdout": stdout, "stderr": stderr})

    return ok(
        {
            "success": True,
            "local_image": local_image,
            "remote_image": remote_image,
            "registry": DOCKER_HOST,
            "push_output": stdout,
        }
    )


@mcp.tool()
async def nexus_download_artifact(
    repository: str,
    artifact_path: str,
    output_path: str,
) -> str:
    """
    Download an artifact from any Nexus repository (Maven, raw, or npm).

    Args:
        repository:    Nexus repository name, e.g. 'maven-releases'
        artifact_path: Path within repository, e.g.
                       'com/company/app/my-service/1.2.0/my-service-1.2.0.jar'
        output_path:   Absolute local path to save the file, e.g.
                       '/tmp/my-service-1.2.0.jar'
    """
    url = f"{BASE_URL}/repository/{repository}/{artifact_path.lstrip('/')}"
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(verify=False) as client:
        async with client.stream("GET", url, auth=AUTH, timeout=300) as r:
            if r.status_code != 200:
                return err(f"Download failed (HTTP {r.status_code})", {"url": url})

            with output.open("wb") as fh:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    fh.write(chunk)

    return ok(
        {
            "success": True,
            "url": url,
            "output_path": str(output),
            "size_bytes": output.stat().st_size,
        }
    )


@mcp.tool()
async def nexus_search_artifacts(
    repository: str,
    group_id: str = "",
    artifact_id: str = "",
    version: str = "",
    name: str = "",
    page_limit: int = 50,
) -> str:
    """
    Search for artifacts in a Nexus repository using the Search API.

    Args:
        repository:  Repository name to scope the search
        group_id:    Maven group ID filter
        artifact_id: Maven artifact ID filter
        version:     Version filter (supports wildcards, e.g. '1.2.*')
        name:        Filename substring filter (for raw/docker repos)
        page_limit:  Max results to return (default 50)
    """
    params: dict = {"repository": repository}
    if group_id:
        params["maven.groupId"] = group_id
    if artifact_id:
        params["maven.artifactId"] = artifact_id
    if version:
        params["version"] = version
    if name:
        params["name"] = name

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/service/rest/v1/search",
            auth=AUTH,
            params=params,
            timeout=30,
        )
        if r.status_code != 200:
            return err("Search failed", r.text)

        items = r.json().get("items", [])[:page_limit]
        return ok(
            {
                "repository": repository,
                "total": len(items),
                "items": [
                    {
                        "id": item["id"],
                        "repository": item["repository"],
                        "format": item["format"],
                        "group": item.get("group"),
                        "name": item["name"],
                        "version": item["version"],
                        "assets": [
                            {"path": a["path"], "download_url": a["downloadUrl"]}
                            for a in item.get("assets", [])
                        ],
                    }
                    for item in items
                ],
            }
        )


@mcp.tool()
async def nexus_check_artifact_exists(
    repository: str,
    artifact_path: str,
) -> str:
    """
    Check if a specific artifact exists in a Nexus repository using a HEAD request.

    Args:
        repository:    Repository name
        artifact_path: Path within the repository
    """
    url = f"{BASE_URL}/repository/{repository}/{artifact_path.lstrip('/')}"

    async with httpx.AsyncClient(verify=False) as client:
        r = await client.head(url, auth=AUTH, timeout=15)

    return ok(
        {
            "exists": r.status_code == 200,
            "url": url,
            "http_status": r.status_code,
        }
    )


@mcp.tool()
async def nexus_list_repositories() -> str:
    """
    List all repositories configured in Nexus with their type and format.
    Useful for discovering repository names before upload/download operations.
    """
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(
            f"{BASE_URL}/service/rest/v1/repositories",
            auth=AUTH,
            timeout=30,
        )
        if r.status_code != 200:
            return err("Failed to list repositories", r.text)

        repos = [
            {
                "name": repo["name"],
                "format": repo["format"],
                "type": repo["type"],
                "url": repo.get("url"),
            }
            for repo in r.json()
        ]
        return ok({"repositories": repos, "total": len(repos)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _init()
    run_server(mcp, default_port=8003)


if __name__ == "__main__":
    main()

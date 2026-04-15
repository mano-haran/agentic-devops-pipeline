# Agentic DevOps Pipeline

An AI-orchestrated CI/CD pipeline for Java Spring Boot applications, built on **Claude Code** with custom **Model Context Protocol (MCP)** servers. The pipeline handles build, code quality, security scanning, and deployment to Red Hat OpenShift — driven by Jira workflow states and delegated across specialised sub-agents.

## Architecture Overview

```
Jira Ticket Transition (manual)
        │
        ▼
Jira Automation Webhook ──▶ Jenkins (UI + logs)
        │
        ▼
Claude Code CLI — Orchestrator Agent
        │
        ├── Crucible  (compile → Docker image → Nexus)
        ├── Inspector (SonarQube quality gate + auto-fix PR)
        ├── Guardian  (Coverity SAST + Black Duck SCA)
        └── Deployer  (Helm → OpenShift SIT/UAT/PROD/DR)
        │
        └── MCP Tool Layer
             ├── stdio mode (per-pipeline): 7 individual servers
             └── sse mode  (hosted):        1 gateway server, 1 port, all tools
```

## Repository Structure

```
agentic-devops-pipeline/
├── pyproject.toml                 # Python package + dependencies (project root)
├── .claude/
│   └── settings.json             # MCP server registrations for Claude Code
├── mcp_servers/
│   ├── __init__.py
│   ├── shared.py                  # Shared auth, HTTP helpers, transport selector
│   ├── gateway.py                 # Unified gateway — all tools, single port (SSE)
│   ├── jira.py
│   ├── jenkins.py
│   ├── nexus.py
│   ├── bitbucket.py
│   ├── sonarqube.py
│   ├── coverity.py
│   ├── blackduck.py
│   ├── test_mcp_client.py         # Standalone CLI test client
│   └── test_all_tools.sh          # Full test suite shell script
├── state/                         # Runtime pipeline state files (gitignored)
├── .env.example                   # Credential template
└── .gitignore
```

---

## MCP Servers & Tools

Tool names follow the **verb-first** convention: `get_issue`, `trigger_build`, `upload_maven_artifact`. In Claude Code agent definitions, tools are referenced as `mcp__<server>__<tool>` — e.g. `mcp__jira__get_issue`.

In the gateway (SSE mode), two tool names are prefixed to resolve cross-server conflicts:
- `sonarqube_list_projects` / `blackduck_list_projects`
- `coverity_get_scan_summary` / `blackduck_get_scan_summary`

---

### 1. Jira Data Center (`mcp_servers.jira`)

Manages Jira issue lifecycle throughout the pipeline. Supports Jira DC Personal Access Tokens.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `get_issue` | Retrieve full issue details — status, assignee, labels, fix versions, custom fields | `issue_key` |
| `get_issue_status` | Lightweight status-only fetch | `issue_key` |
| `get_transitions` | List available workflow transitions in the current state | `issue_key` |
| `transition_issue` | Move an issue to a new workflow state | `issue_key`, `transition_id`, `comment` _(opt)_, `resolution` _(opt)_ |
| `update_issue` | Update specific fields — summary, description, labels, fix version, assignee, custom fields | `issue_key`, any of: `summary`, `description`, `labels`, `fix_version`, `assignee`, `custom_fields_json` |
| `add_comment` | Add a comment (supports Jira wiki markup) | `issue_key`, `comment` |
| `create_issue` | Create a new issue — used to auto-raise UAT/PROD/DR tickets on SIT completion | `project_key`, `issue_type`, `summary`, `description` _(opt)_, `parent_key` _(opt)_, `labels` _(opt)_, `fix_version` _(opt)_ |
| `get_project_versions` | List all defined versions in a project | `project_key` |

**Required env vars:** `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_USERNAME`

---

### 2. Jenkins (`mcp_servers.jenkins`)

Triggers and monitors Jenkins builds. Handles CSRF crumb and folder-based job paths (`folder/subfolder/job`).

| Tool | Description | Arguments |
|------|-------------|-----------|
| `trigger_build` | Trigger a job with optional parameters; polls queue until build number is assigned | `job_name`, `parameters_json` _(opt)_, `wait_for_start` _(opt)_ |
| `get_build_status` | Get result, duration, and build state for a specific build | `job_name`, `build_number` |
| `get_last_build` | Get status of the most recent build | `job_name` |
| `get_console_output` | Stream console log with byte-offset pagination for large logs | `job_name`, `build_number`, `start_byte` _(opt)_ |
| `get_job_info` | Job metadata — parameters, health report, last success/failure | `job_name` |
| `wait_for_build` | Poll until a build completes or timeout is reached | `job_name`, `build_number`, `poll_interval_seconds` _(opt)_, `timeout_seconds` _(opt)_ |

**Required env vars:** `JENKINS_BASE_URL`, `JENKINS_USERNAME`, `JENKINS_API_TOKEN`

---

### 3. Nexus Repository Manager 3 (`mcp_servers.nexus`)

All artifact operations — Maven upload, raw file upload, Docker image push, and download. Docker operations delegate to the Docker CLI on the VM.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `upload_maven_artifact` | Upload JAR/WAR/POM to a Maven hosted repository | `repository`, `group_id`, `artifact_id`, `version`, `file_path`, `packaging` _(opt)_, `classifier` _(opt)_ |
| `upload_raw_artifact` | Upload any file to a raw hosted repository | `repository`, `directory`, `file_path`, `destination_filename` _(opt)_ |
| `upload_docker_image` | Tag and push a local Docker image to the Nexus Docker registry | `local_image`, `image_tag`, `nexus_repo_path` _(opt)_ |
| `download_artifact` | Download an artifact from any repository to a local path | `repository`, `artifact_path`, `output_path` |
| `search_artifacts` | Search by group, artifact ID, or version (supports wildcards) | `repository`, `group_id` _(opt)_, `artifact_id` _(opt)_, `version` _(opt)_, `name` _(opt)_ |
| `check_artifact_exists` | HEAD check to verify an artifact path exists | `repository`, `artifact_path` |
| `list_repositories` | List all repositories with format and type | — |

**Required env vars:** `NEXUS_BASE_URL`, `NEXUS_USERNAME`, `NEXUS_PASSWORD`, `NEXUS_DOCKER_HOST`, `NEXUS_DOCKER_REPO`

---

### 4. Bitbucket Data Center (`mcp_servers.bitbucket`)

Repository operations via the Bitbucket DC REST API and git CLI. Clone, push, and tag use the git CLI with token-embedded HTTPS URLs.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `clone_repo` | Clone a repo to a local directory via git CLI | `project_key`, `repo_slug`, `target_dir`, `branch` _(opt)_, `depth` _(opt)_ |
| `create_pr` | Open a pull request with optional reviewer list | `project_key`, `repo_slug`, `title`, `description`, `source_branch`, `target_branch`, `reviewer_usernames` _(opt)_ |
| `get_pr` | Get PR details — state, reviewers, approvals, merge-readiness | `project_key`, `repo_slug`, `pr_id` |
| `merge_pr` | Merge an approved pull request | `project_key`, `repo_slug`, `pr_id`, `merge_strategy` _(opt)_, `message` _(opt)_ |
| `list_open_prs` | List open PRs with optional branch and author filters | `project_key`, `repo_slug`, `target_branch` _(opt)_, `author_username` _(opt)_, `limit` _(opt)_ |
| `create_tag` | Create an annotated git tag and optionally push it | `repo_dir`, `tag_name`, `message`, `commit_sha` _(opt)_, `push` _(opt)_ |
| `push_branch` | Push a local branch to origin | `repo_dir`, `branch_name`, `set_upstream` _(opt)_ |
| `get_pr_diff` | File-level diff for a pull request | `project_key`, `repo_slug`, `pr_id` |
| `get_commit_diff` | Diff between two commits | `project_key`, `repo_slug`, `since_commit`, `until_commit` _(opt)_, `context_lines` _(opt)_ |

**Required env vars:** `BITBUCKET_BASE_URL`, `BITBUCKET_API_TOKEN`, `BITBUCKET_USERNAME`

---

### 5. SonarQube (`mcp_servers.sonarqube`)

Quality gate checks, metric retrieval, and issue inspection with remediation guidance. Used by the Inspector agent to detect and auto-fix code quality issues.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `get_quality_gate_status` | Overall gate result (OK/ERROR) with per-condition breakdown | `project_key`, `branch` _(opt)_, `pull_request` _(opt)_ |
| `get_metrics` | Retrieve metric values — bugs, coverage, duplication, ratings | `project_key`, `branch` _(opt)_, `metric_keys` _(opt, comma-separated)_ |
| `get_issues` | Paginated list of issues filtered by severity and type | `project_key`, `branch` _(opt)_, `severities` _(opt)_, `types` _(opt)_, `statuses` _(opt)_, `page`, `page_size` |
| `get_issue_suggestions` | Issues enriched with rule descriptions and remediation guidance | `project_key`, `branch` _(opt)_, `severities` _(opt)_, `types` _(opt)_ |
| `get_new_code_issues` | Issues introduced in the new code period (leak period) only | `project_key`, `branch` _(opt)_, `severities` _(opt)_ |
| `get_project_analysis_status` | Check if a completed analysis exists and when it ran | `project_key`, `branch` _(opt)_ |
| `list_projects` | List all projects with last analysis date | `search` _(opt)_, `page_size` _(opt)_ |

**Required env vars:** `SONAR_BASE_URL`, `SONAR_API_TOKEN`

---

### 6. Synopsys Coverity (`mcp_servers.coverity`)

SAST results from Coverity Connect on-prem. Targets the Coverity Connect REST API v2 (Coverity 2021.06+).

**Pipeline gate:** High impact > 0 → BLOCK · Medium impact > 5 → BLOCK

| Tool | Description | Arguments |
|------|-------------|-----------|
| `get_projects` | List all accessible Coverity projects | — |
| `get_streams` | List streams (branch/config mappings) | `project_name` _(opt)_ |
| `get_defects` | Retrieve defects with impact, status, and checker filters | `project_name`, `stream_name` _(opt)_, `impact` _(opt)_, `status` _(opt)_, `checker` _(opt)_, `page_size`, `offset` |
| `get_defect_details` | Full event trace and remediation guidance for a specific CID | `cid`, `project_name` |
| `get_scan_summary` | Pipeline-ready PASSED/FAILED verdict with blocking reasons | `project_name`, `stream_name` _(opt)_ |
| `get_snapshots` | List recent analysis snapshots for a stream | `project_name`, `stream_name`, `limit` _(opt)_ |

**Required env vars:** `COVERITY_BASE_URL`, `COVERITY_USERNAME`, `COVERITY_API_TOKEN`

---

### 7. Synopsys Black Duck (`mcp_servers.blackduck`)

SCA — open-source vulnerability scanning and licence policy enforcement. Uses the Black Duck REST API v6 with token-exchange authentication.

**Pipeline gate:** CVSS ≥ 9.0 → BLOCK · CVSS ≥ 7.0 → BLOCK · Policy `IN_VIOLATION` → BLOCK

| Tool | Description | Arguments |
|------|-------------|-----------|
| `list_projects` | List Black Duck projects with optional name filter | `search` _(opt)_, `limit` _(opt)_ |
| `list_project_versions` | List scan versions for a project, newest first | `project_name`, `limit` _(opt)_ |
| `get_vulnerabilities` | Vulnerable components with CVSS scores and remediation status | `project_name`, `version_name`, `min_cvss_score` _(opt)_, `remediated` _(opt)_, `limit` _(opt)_ |
| `get_vulnerability_details` | Full CVE detail — description, CVSS vector, CWE, fix guidance | `project_name`, `version_name`, `vulnerability_name` |
| `get_policy_violations` | Components in violation of open-source policies | `project_name`, `version_name` |
| `get_components` | Full Bill of Materials — all detected open-source components | `project_name`, `version_name`, `limit` _(opt)_ |
| `get_scan_summary` | Pipeline-ready PASSED/FAILED verdict combining vulns and policy | `project_name`, `version_name` |

**Required env vars:** `BLACKDUCK_BASE_URL`, `BLACKDUCK_API_TOKEN`

---

## Installation

### Prerequisites

- Python 3.11+
- `git` CLI configured with credentials on the VM
- `docker` CLI (for Nexus Docker image push)
- Claude Code installed on the VM

### Install

```bash
git clone https://github.com/mano-haran/agentic-devops-pipeline.git
cd agentic-devops-pipeline
pip install -e .
cp .env.example .env
# Edit .env with your credentials
```

---

## Environment Variables

Copy `.env.example` to `.env`. The `.env` file is gitignored and must never be committed. In production, credentials are injected by the Jenkins **Credentials Binding** plugin into the shell session that invokes Claude Code; the MCP servers inherit them from the process environment.

| Variable | Used by |
|----------|---------|
| `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_USERNAME` | jira |
| `BITBUCKET_BASE_URL`, `BITBUCKET_API_TOKEN`, `BITBUCKET_USERNAME` | bitbucket |
| `JENKINS_BASE_URL`, `JENKINS_USERNAME`, `JENKINS_API_TOKEN` | jenkins |
| `SONAR_BASE_URL`, `SONAR_API_TOKEN` | sonarqube |
| `NEXUS_BASE_URL`, `NEXUS_USERNAME`, `NEXUS_PASSWORD`, `NEXUS_DOCKER_HOST`, `NEXUS_DOCKER_REPO` | nexus |
| `COVERITY_BASE_URL`, `COVERITY_USERNAME`, `COVERITY_API_TOKEN` | coverity |
| `BLACKDUCK_BASE_URL`, `BLACKDUCK_API_TOKEN` | blackduck |
| `MCP_TRANSPORT` | All servers — `stdio` (default) or `sse` |
| `MCP_HOST` | SSE mode — bind address (default: `127.0.0.1`) |
| `MCP_PORT` | SSE mode — gateway port (default: `8000`) |

---

## Testing MCP Servers Standalone

Run tests from the project root (`/opt/smart-devops`). The test client reads `.env` automatically.

### List all tools in a server

`--list-tools` connects to the server over stdio, calls `list_tools` via the MCP protocol, and prints each tool's **full input schema** — parameter names, types, required/optional status, defaults, and descriptions. This is the authoritative source of truth for what each tool accepts.

```bash
python mcp_servers/test_mcp_client.py mcp_servers.gateway   --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.jira       --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.jenkins    --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.nexus      --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket  --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube  --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.coverity   --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.blackduck  --list-tools
```

Use `mcp_servers.gateway --list-tools` to see all 47 tools in one output.

### Call a specific tool

Arguments are passed as `key=value` pairs. JSON values (booleans, numbers, nested objects) are parsed automatically — pass them quoted.

#### Jira

```bash
python mcp_servers/test_mcp_client.py mcp_servers.jira get_issue issue_key=PROJ-123
python mcp_servers/test_mcp_client.py mcp_servers.jira get_issue_status issue_key=PROJ-123
python mcp_servers/test_mcp_client.py mcp_servers.jira get_transitions issue_key=PROJ-123
python mcp_servers/test_mcp_client.py mcp_servers.jira get_project_versions project_key=PROJ
python mcp_servers/test_mcp_client.py mcp_servers.jira add_comment issue_key=PROJ-123 comment="Pipeline started on release/1.2.0"
python mcp_servers/test_mcp_client.py mcp_servers.jira update_issue issue_key=PROJ-123 labels=smart-devops,pipeline fix_version=1.2.0
python mcp_servers/test_mcp_client.py mcp_servers.jira transition_issue issue_key=PROJ-123 transition_id=21 comment="Build started by SmartDevOps"
python mcp_servers/test_mcp_client.py mcp_servers.jira create_issue project_key=PROJ issue_type=Task summary="[SmartDevOps] UAT Deployment - my-service 1.2.0" description="Auto-created on SIT success" fix_version=1.2.0
```

#### Jenkins

```bash
python mcp_servers/test_mcp_client.py mcp_servers.jenkins get_job_info job_name=smart-devops/build-app
python mcp_servers/test_mcp_client.py mcp_servers.jenkins get_last_build job_name=smart-devops/build-app
python mcp_servers/test_mcp_client.py mcp_servers.jenkins get_build_status job_name=smart-devops/build-app build_number=42
python mcp_servers/test_mcp_client.py mcp_servers.jenkins get_console_output job_name=smart-devops/build-app build_number=42 start_byte=0
python mcp_servers/test_mcp_client.py mcp_servers.jenkins wait_for_build job_name=smart-devops/build-app build_number=42 poll_interval_seconds=15 timeout_seconds=900
python mcp_servers/test_mcp_client.py mcp_servers.jenkins trigger_build job_name=smart-devops/build-app parameters_json='{"BRANCH":"release/1.2.0","JIRA_TICKET":"PROJ-123"}' wait_for_start=true
```

#### Nexus

```bash
python mcp_servers/test_mcp_client.py mcp_servers.nexus list_repositories
python mcp_servers/test_mcp_client.py mcp_servers.nexus search_artifacts repository=maven-releases group_id=com.company artifact_id=my-service
python mcp_servers/test_mcp_client.py mcp_servers.nexus check_artifact_exists repository=maven-releases artifact_path=com/company/app/my-service/1.2.0/my-service-1.2.0.jar
python mcp_servers/test_mcp_client.py mcp_servers.nexus download_artifact repository=maven-releases artifact_path=com/company/app/my-service/1.2.0/my-service-1.2.0.jar output_path=/tmp/my-service-1.2.0.jar
python mcp_servers/test_mcp_client.py mcp_servers.nexus upload_raw_artifact repository=raw-hosted directory=/smartdevops-tests/ file_path=/tmp/test-report.txt
python mcp_servers/test_mcp_client.py mcp_servers.nexus upload_maven_artifact repository=maven-snapshots group_id=com.company.app artifact_id=my-service version=1.2.0-SNAPSHOT file_path=/path/to/my-service-1.2.0-SNAPSHOT.jar packaging=jar
python mcp_servers/test_mcp_client.py mcp_servers.nexus upload_docker_image local_image=my-service:1.2.0 image_tag=1.2.0-PROJ-123
```

#### Bitbucket

```bash
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket list_open_prs project_key=PROJ repo_slug=my-service
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket get_pr project_key=PROJ repo_slug=my-service pr_id=42
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket get_pr_diff project_key=PROJ repo_slug=my-service pr_id=42
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket get_commit_diff project_key=PROJ repo_slug=my-service since_commit=abc1234 until_commit=def5678
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket clone_repo project_key=PROJ repo_slug=my-service target_dir=/tmp/my-service-clone branch=release/1.2.0 depth=1
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket create_tag repo_dir=/tmp/my-service-clone tag_name=v1.2.0 message="Release 1.2.0 - PROJ-123" push=false
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket push_branch repo_dir=/tmp/my-service-clone branch_name=fix/sonar-PROJ-123
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket create_pr project_key=PROJ repo_slug=my-service title="[SmartDevOps] Sonar auto-fix for PROJ-123" description="Auto-generated fixes. Review required." source_branch=fix/sonar-PROJ-123 target_branch=develop reviewer_usernames=john.smith,jane.doe
python mcp_servers/test_mcp_client.py mcp_servers.bitbucket merge_pr project_key=PROJ repo_slug=my-service pr_id=42 merge_strategy=merge-commit
```

#### SonarQube

```bash
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube list_projects search=my-service
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube get_quality_gate_status project_key=com.company:my-service branch=release/1.2.0
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube get_metrics project_key=com.company:my-service branch=release/1.2.0
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube get_project_analysis_status project_key=com.company:my-service branch=release/1.2.0
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube get_issues project_key=com.company:my-service severities=BLOCKER,CRITICAL types=BUG,VULNERABILITY page_size=20
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube get_issue_suggestions project_key=com.company:my-service severities=BLOCKER,CRITICAL
python mcp_servers/test_mcp_client.py mcp_servers.sonarqube get_new_code_issues project_key=com.company:my-service branch=release/1.2.0
```

#### Coverity

```bash
python mcp_servers/test_mcp_client.py mcp_servers.coverity get_projects
python mcp_servers/test_mcp_client.py mcp_servers.coverity get_streams project_name=my-service
python mcp_servers/test_mcp_client.py mcp_servers.coverity get_defects project_name=my-service stream_name=my-service-main page_size=20
python mcp_servers/test_mcp_client.py mcp_servers.coverity get_defect_details cid=12345 project_name=my-service
python mcp_servers/test_mcp_client.py mcp_servers.coverity get_scan_summary project_name=my-service stream_name=my-service-main
python mcp_servers/test_mcp_client.py mcp_servers.coverity get_snapshots project_name=my-service stream_name=my-service-main limit=5
```

#### Black Duck

```bash
python mcp_servers/test_mcp_client.py mcp_servers.blackduck list_projects search=my-service
python mcp_servers/test_mcp_client.py mcp_servers.blackduck list_project_versions project_name=my-service
python mcp_servers/test_mcp_client.py mcp_servers.blackduck get_vulnerabilities project_name=my-service version_name=1.2.0 min_cvss_score=7.0
python mcp_servers/test_mcp_client.py mcp_servers.blackduck get_vulnerability_details project_name=my-service version_name=1.2.0 vulnerability_name=CVE-2021-44228
python mcp_servers/test_mcp_client.py mcp_servers.blackduck get_policy_violations project_name=my-service version_name=1.2.0
python mcp_servers/test_mcp_client.py mcp_servers.blackduck get_components project_name=my-service version_name=1.2.0 limit=50
python mcp_servers/test_mcp_client.py mcp_servers.blackduck get_scan_summary project_name=my-service version_name=1.2.0
```

### Run the full test suite

The suite runs all read-only calls against your live instances. State-mutating tools (transitions, PR creation, build triggers) are present in the script but commented out — opt in by editing `test_all_tools.sh`.

```bash
# Set test targets (override defaults)
export JIRA_TEST_TICKET=PROJ-123
export JENKINS_TEST_JOB=smart-devops/build-app
export SONAR_TEST_PROJECT=com.company:my-service
export COVERITY_TEST_PROJECT=my-service
export COVERITY_TEST_STREAM=my-service-main
export BLACKDUCK_TEST_PROJECT=my-service
export BLACKDUCK_TEST_VERSION=1.2.0
export BITBUCKET_TEST_PROJECT=PROJ
export BITBUCKET_TEST_REPO=my-service

./mcp_servers/test_all_tools.sh                              # all 7 servers
./mcp_servers/test_all_tools.sh jira                         # single server
./mcp_servers/test_all_tools.sh coverity blackduck           # multiple servers
./mcp_servers/test_all_tools.sh jira jenkins sonarqube       # multiple servers
```

The test runner exits non-zero on any failure, making it safe to run as a Jenkins verification step.

---

## MCP Transport Modes

The servers support two transport modes. **Use one, not both.** The mode is selected via the `MCP_TRANSPORT` environment variable.

---

### stdio mode — recommended for production pipelines

Claude Code spawns each server as a subprocess, communicating over stdin/stdout. Credentials flow in per-session via Jenkins environment injection — the correct model for isolated, per-pipeline execution.

**`.claude/settings.json`:**
```json
{
  "mcpServers": {
    "jira":      { "command": "python", "args": ["-m", "mcp_servers.jira"],      "cwd": "/opt/smart-devops" },
    "jenkins":   { "command": "python", "args": ["-m", "mcp_servers.jenkins"],   "cwd": "/opt/smart-devops" },
    "nexus":     { "command": "python", "args": ["-m", "mcp_servers.nexus"],     "cwd": "/opt/smart-devops" },
    "bitbucket": { "command": "python", "args": ["-m", "mcp_servers.bitbucket"], "cwd": "/opt/smart-devops" },
    "sonarqube": { "command": "python", "args": ["-m", "mcp_servers.sonarqube"], "cwd": "/opt/smart-devops" },
    "coverity":  { "command": "python", "args": ["-m", "mcp_servers.coverity"],  "cwd": "/opt/smart-devops" },
    "blackduck": { "command": "python", "args": ["-m", "mcp_servers.blackduck"], "cwd": "/opt/smart-devops" }
  }
}
```

No `MCP_TRANSPORT` variable needed — `stdio` is the default.

In agent definition files (`.claude/agents/*.md`), reference tools as:
```yaml
tools:
  - mcp__jira__get_issue
  - mcp__jira__transition_issue
  - mcp__jenkins__trigger_build
  - mcp__jenkins__wait_for_build
```

---

### SSE mode — for standalone testing or shared team server

The **gateway server** (`mcp_servers.gateway`) aggregates all 7 servers into one FastMCP instance and serves all 47 tools over a single HTTP endpoint. Claude Code connects via URL instead of spawning subprocesses.

**Start the gateway:**
```bash
# Foreground (for testing)
MCP_TRANSPORT=sse MCP_PORT=8000 python -m mcp_servers.gateway

# Background with nohup
MCP_TRANSPORT=sse MCP_PORT=8000 nohup python -m mcp_servers.gateway > /tmp/mcp-gateway.log 2>&1 &

# Verify it's running
curl http://127.0.0.1:8000/sse
```

**`.claude/settings.json` for SSE mode** (replace the `mcpServers` block):
```json
{
  "mcpServers": {
    "smart-devops": {
      "url": "http://127.0.0.1:8000/sse"
    }
  }
}
```

**Test the gateway via the MCP Inspector UI** (interactive browser tool):
```bash
mcp dev mcp_servers/gateway.py
# Opens http://localhost:5173 — call any tool interactively
```

**Test the gateway via the CLI test client:**
```bash
python mcp_servers/test_mcp_client.py mcp_servers.gateway --list-tools
python mcp_servers/test_mcp_client.py mcp_servers.gateway get_issue issue_key=PROJ-123
python mcp_servers/test_mcp_client.py mcp_servers.gateway get_quality_gate_status project_key=com.company:my-service
```

**Transport comparison:**

| | stdio | SSE (gateway) |
|---|---|---|
| Claude Code config | `command` (7 entries) | `url` (1 entry) |
| Server lifecycle | Claude Code spawns/kills | Persistent daemon |
| Credential injection | Per-session via Jenkins env | At daemon startup |
| Pipeline isolation | Each build gets own process | Shared across all sessions |
| Port management | None needed | 1 port (default 8000) |
| Best for | Production CI/CD pipelines | Local testing, shared teams |

---

## Security Notes

- All credentials are consumed from environment variables — never written to files or logged
- `.env` is gitignored; use Jenkins **Credentials Binding** plugin in production
- HTTP clients use `verify=False` for internal TLS — replace with your CA bundle path (`verify="/etc/pki/ca-trust/..."`) for production hardening
- Git clone URLs embed the API token; the test client masks tokens in all output
- OpenShift uses service account tokens scoped per namespace (SIT/UAT/PROD/DR)

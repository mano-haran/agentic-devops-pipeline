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
├── .claude/
│   └── settings.json          # MCP server registrations for Claude Code
├── mcp_servers/
│   ├── pyproject.toml         # Python package + dependencies
│   ├── shared/utils.py        # Shared auth, HTTP helpers, transport selector
│   ├── mcp_gateway/server.py  # Unified gateway — all tools, single port (SSE)
│   ├── mcp_jira/server.py
│   ├── mcp_jenkins/server.py
│   ├── mcp_nexus/server.py
│   ├── mcp_bitbucket/server.py
│   ├── mcp_sonarqube/server.py
│   ├── mcp_coverity/server.py
│   ├── mcp_blackduck/server.py
│   ├── test_mcp_client.py     # Standalone CLI test client
│   └── test_all_tools.sh      # Full test suite shell script
├── state/                     # Runtime pipeline state files (gitignored)
├── .env.example               # Credential template
└── .gitignore
```

---

## MCP Servers & Tools

### 1. Jira Data Center (`mcp_jira`)

Manages Jira issue lifecycle throughout the pipeline. Supports Jira DC Personal Access Tokens.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `jira_get_issue` | Retrieve full issue details — status, assignee, labels, fix versions, custom fields | `issue_key` |
| `jira_get_issue_status` | Lightweight status-only fetch | `issue_key` |
| `jira_get_transitions` | List available workflow transitions in the current state | `issue_key` |
| `jira_transition_issue` | Move an issue to a new workflow state | `issue_key`, `transition_id`, `comment` _(opt)_, `resolution` _(opt)_ |
| `jira_update_issue` | Update specific fields — summary, description, labels, fix version, assignee, custom fields | `issue_key`, any of: `summary`, `description`, `labels`, `fix_version`, `assignee`, `custom_fields_json` |
| `jira_add_comment` | Add a comment (supports Jira wiki markup) | `issue_key`, `comment` |
| `jira_create_issue` | Create a new issue — used to auto-raise UAT/PROD/DR tickets on SIT completion | `project_key`, `issue_type`, `summary`, `description` _(opt)_, `parent_key` _(opt)_, `labels` _(opt)_, `fix_version` _(opt)_ |
| `jira_get_project_versions` | List all defined versions in a project | `project_key` |

**Required env vars:** `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_USERNAME`

---

### 2. Jenkins (`mcp_jenkins`)

Triggers and monitors Jenkins builds. Handles CSRF crumb and folder-based job paths (`folder/subfolder/job`).

| Tool | Description | Arguments |
|------|-------------|-----------|
| `jenkins_trigger_build` | Trigger a job with optional parameters; polls queue until build number is assigned | `job_name`, `parameters_json` _(opt)_, `wait_for_start` _(opt)_ |
| `jenkins_get_build_status` | Get result, duration, and build state for a specific build | `job_name`, `build_number` |
| `jenkins_get_last_build` | Get status of the most recent build | `job_name` |
| `jenkins_get_console_output` | Stream console log with byte-offset pagination for large logs | `job_name`, `build_number`, `start_byte` _(opt)_ |
| `jenkins_get_job_info` | Job metadata — parameters, health report, last success/failure | `job_name` |
| `jenkins_wait_for_build` | Poll until a build completes or timeout is reached | `job_name`, `build_number`, `poll_interval_seconds` _(opt)_, `timeout_seconds` _(opt)_ |

**Required env vars:** `JENKINS_BASE_URL`, `JENKINS_USERNAME`, `JENKINS_API_TOKEN`

---

### 3. Nexus Repository Manager 3 (`mcp_nexus`)

All artifact operations — Maven upload, raw file upload, Docker image push, and download. Docker operations delegate to the Docker CLI on the VM.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `nexus_upload_maven_artifact` | Upload JAR/WAR/POM to a Maven hosted repository | `repository`, `group_id`, `artifact_id`, `version`, `file_path`, `packaging` _(opt)_, `classifier` _(opt)_ |
| `nexus_upload_raw_artifact` | Upload any file to a raw hosted repository | `repository`, `directory`, `file_path`, `destination_filename` _(opt)_ |
| `nexus_upload_docker_image` | Tag and push a local Docker image to the Nexus Docker registry | `local_image`, `image_tag`, `nexus_repo_path` _(opt)_ |
| `nexus_download_artifact` | Download an artifact from any repository to a local path | `repository`, `artifact_path`, `output_path` |
| `nexus_search_artifacts` | Search by group, artifact ID, or version (supports wildcards) | `repository`, `group_id` _(opt)_, `artifact_id` _(opt)_, `version` _(opt)_, `name` _(opt)_ |
| `nexus_check_artifact_exists` | HEAD check to verify an artifact path exists | `repository`, `artifact_path` |
| `nexus_list_repositories` | List all repositories with format and type | — |

**Required env vars:** `NEXUS_BASE_URL`, `NEXUS_USERNAME`, `NEXUS_PASSWORD`, `NEXUS_DOCKER_HOST`, `NEXUS_DOCKER_REPO`

---

### 4. Bitbucket Data Center (`mcp_bitbucket`)

Repository operations via the Bitbucket DC REST API and git CLI. Clone, push, and tag use the git CLI with token-embedded HTTPS URLs.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `bitbucket_clone_repo` | Clone a repo to a local directory via git CLI | `project_key`, `repo_slug`, `target_dir`, `branch` _(opt)_, `depth` _(opt)_ |
| `bitbucket_create_pr` | Open a pull request with optional reviewer list | `project_key`, `repo_slug`, `title`, `description`, `source_branch`, `target_branch`, `reviewer_usernames` _(opt)_ |
| `bitbucket_get_pr` | Get PR details — state, reviewers, approvals, merge-readiness | `project_key`, `repo_slug`, `pr_id` |
| `bitbucket_merge_pr` | Merge an approved pull request | `project_key`, `repo_slug`, `pr_id`, `merge_strategy` _(opt)_, `message` _(opt)_ |
| `bitbucket_list_open_prs` | List open PRs with optional branch and author filters | `project_key`, `repo_slug`, `target_branch` _(opt)_, `author_username` _(opt)_, `limit` _(opt)_ |
| `bitbucket_create_tag` | Create an annotated git tag and optionally push it | `repo_dir`, `tag_name`, `message`, `commit_sha` _(opt)_, `push` _(opt)_ |
| `bitbucket_push_branch` | Push a local branch to origin | `repo_dir`, `branch_name`, `set_upstream` _(opt)_ |
| `bitbucket_get_pr_diff` | File-level diff for a pull request | `project_key`, `repo_slug`, `pr_id` |
| `bitbucket_get_commit_diff` | Diff between two commits | `project_key`, `repo_slug`, `since_commit`, `until_commit` _(opt)_, `context_lines` _(opt)_ |

**Required env vars:** `BITBUCKET_BASE_URL`, `BITBUCKET_API_TOKEN`, `BITBUCKET_USERNAME`

---

### 5. SonarQube (`mcp_sonarqube`)

Quality gate checks, metric retrieval, and issue inspection with remediation guidance. Used by the Inspector agent to detect and auto-fix code quality issues.

| Tool | Description | Arguments |
|------|-------------|-----------|
| `sonar_get_quality_gate_status` | Overall gate result (OK/ERROR) with per-condition breakdown | `project_key`, `branch` _(opt)_, `pull_request` _(opt)_ |
| `sonar_get_metrics` | Retrieve metric values — bugs, coverage, duplication, ratings | `project_key`, `branch` _(opt)_, `metric_keys` _(opt, comma-separated)_ |
| `sonar_get_issues` | Paginated list of issues filtered by severity and type | `project_key`, `branch` _(opt)_, `severities` _(opt)_, `types` _(opt)_, `statuses` _(opt)_, `page`, `page_size` |
| `sonar_get_issue_suggestions` | Issues enriched with rule descriptions and remediation guidance | `project_key`, `branch` _(opt)_, `severities` _(opt)_, `types` _(opt)_ |
| `sonar_get_new_code_issues` | Issues introduced in the new code period (leak period) only | `project_key`, `branch` _(opt)_, `severities` _(opt)_ |
| `sonar_get_project_analysis_status` | Check if a completed analysis exists and when it ran | `project_key`, `branch` _(opt)_ |
| `sonar_list_projects` | List all projects with last analysis date | `search` _(opt)_, `page_size` _(opt)_ |

**Required env vars:** `SONAR_BASE_URL`, `SONAR_API_TOKEN`

---

### 6. Synopsys Coverity (`mcp_coverity`)

SAST results from Coverity Connect on-prem. Targets the Coverity Connect REST API v2 (Coverity 2021.06+).

**Pipeline gate:** High impact > 0 → BLOCK · Medium impact > 5 → BLOCK

| Tool | Description | Arguments |
|------|-------------|-----------|
| `coverity_get_projects` | List all accessible Coverity projects | — |
| `coverity_get_streams` | List streams (branch/config mappings) | `project_name` _(opt)_ |
| `coverity_get_defects` | Retrieve defects with impact, status, and checker filters | `project_name`, `stream_name` _(opt)_, `impact` _(opt)_, `status` _(opt)_, `checker` _(opt)_, `page_size`, `offset` |
| `coverity_get_defect_details` | Full event trace and remediation guidance for a specific CID | `cid`, `project_name` |
| `coverity_get_scan_summary` | Pipeline-ready PASSED/FAILED verdict with blocking reasons | `project_name`, `stream_name` _(opt)_ |
| `coverity_get_snapshots` | List recent analysis snapshots for a stream | `project_name`, `stream_name`, `limit` _(opt)_ |

**Required env vars:** `COVERITY_BASE_URL`, `COVERITY_USERNAME`, `COVERITY_API_TOKEN`

---

### 7. Synopsys Black Duck (`mcp_blackduck`)

SCA — open-source vulnerability scanning and licence policy enforcement. Uses the Black Duck REST API v6 with token-exchange authentication.

**Pipeline gate:** CVSS ≥ 9.0 → BLOCK · CVSS ≥ 7.0 → BLOCK · Policy `IN_VIOLATION` → BLOCK

| Tool | Description | Arguments |
|------|-------------|-----------|
| `blackduck_list_projects` | List Black Duck projects with optional name filter | `search` _(opt)_, `limit` _(opt)_ |
| `blackduck_list_project_versions` | List scan versions for a project, newest first | `project_name`, `limit` _(opt)_ |
| `blackduck_get_vulnerabilities` | Vulnerable components with CVSS scores and remediation status | `project_name`, `version_name`, `min_cvss_score` _(opt)_, `remediated` _(opt)_, `limit` _(opt)_ |
| `blackduck_get_vulnerability_details` | Full CVE detail — description, CVSS vector, CWE, fix guidance | `project_name`, `version_name`, `vulnerability_name` |
| `blackduck_get_policy_violations` | Components in violation of open-source policies | `project_name`, `version_name` |
| `blackduck_get_components` | Full Bill of Materials — all detected open-source components | `project_name`, `version_name`, `limit` _(opt)_ |
| `blackduck_get_scan_summary` | Pipeline-ready PASSED/FAILED verdict combining vulns and policy | `project_name`, `version_name` |

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
cd agentic-devops-pipeline/mcp_servers
pip install -e .
cp ../.env.example ../.env
# Edit ../.env with your credentials
```

---

## Environment Variables

Copy `.env.example` to `.env`. The `.env` file is gitignored and must never be committed. In production, credentials are injected by the Jenkins **Credentials Binding** plugin into the shell session that invokes Claude Code; the MCP servers inherit them from the process environment.

| Variable | Used by |
|----------|---------|
| `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_USERNAME` | mcp_jira |
| `BITBUCKET_BASE_URL`, `BITBUCKET_API_TOKEN`, `BITBUCKET_USERNAME` | mcp_bitbucket |
| `JENKINS_BASE_URL`, `JENKINS_USERNAME`, `JENKINS_API_TOKEN` | mcp_jenkins |
| `SONAR_BASE_URL`, `SONAR_API_TOKEN` | mcp_sonarqube |
| `NEXUS_BASE_URL`, `NEXUS_USERNAME`, `NEXUS_PASSWORD`, `NEXUS_DOCKER_HOST`, `NEXUS_DOCKER_REPO` | mcp_nexus |
| `COVERITY_BASE_URL`, `COVERITY_USERNAME`, `COVERITY_API_TOKEN` | mcp_coverity |
| `BLACKDUCK_BASE_URL`, `BLACKDUCK_API_TOKEN` | mcp_blackduck |
| `MCP_TRANSPORT` | All servers — `stdio` (default) or `sse` |
| `MCP_HOST` | SSE mode — bind address (default: `127.0.0.1`) |
| `MCP_PORT` | SSE mode — gateway port (default: `8000`) |

---

## Testing MCP Servers Standalone

All tests run from `mcp_servers/`. The test client reads `.env` from the parent directory automatically.

```bash
cd agentic-devops-pipeline/mcp_servers
```

### List all tools in a server

`--list-tools` connects to the server over stdio, calls `list_tools` via the MCP protocol, and prints each tool's **full input schema** — parameter names, types, required/optional status, defaults, and descriptions. This is the authoritative source of truth for what each tool accepts.

```bash
python test_mcp_client.py mcp_gateway.server --list-tools
python test_mcp_client.py mcp_jira.server --list-tools
python test_mcp_client.py mcp_jenkins.server --list-tools
python test_mcp_client.py mcp_nexus.server --list-tools
python test_mcp_client.py mcp_bitbucket.server --list-tools
python test_mcp_client.py mcp_sonarqube.server --list-tools
python test_mcp_client.py mcp_coverity.server --list-tools
python test_mcp_client.py mcp_blackduck.server --list-tools
```

Use `mcp_gateway.server --list-tools` to see all 47 tools in one output.

Example output for `mcp_jira.server --list-tools`:
```
Server : mcp_jira.server
Tools  : 8

============================================================

  jira_get_issue
    Retrieve full issue details including status, assignee,
    description, labels, fix versions and custom fields.

    Arguments:
      issue_key [required]  (string)
        Jira issue key, e.g. 'PROJ-123'

  jira_transition_issue
    Transition a Jira issue to a new workflow state.
    Use jira_get_transitions first to find the correct transition_id.

    Arguments:
      issue_key     [required]  (string)
      transition_id [required]  (string)
      comment       [optional]  (string, default='')
      resolution    [optional]  (string, default='')
...
============================================================
```

### Call a specific tool

Arguments are passed as `key=value` pairs on the same line. JSON values (booleans, numbers, nested objects) are parsed automatically — pass them quoted.

#### Jira

```bash
python test_mcp_client.py mcp_jira.server jira_get_issue issue_key=PROJ-123
python test_mcp_client.py mcp_jira.server jira_get_issue_status issue_key=PROJ-123
python test_mcp_client.py mcp_jira.server jira_get_transitions issue_key=PROJ-123
python test_mcp_client.py mcp_jira.server jira_get_project_versions project_key=PROJ
python test_mcp_client.py mcp_jira.server jira_add_comment issue_key=PROJ-123 comment="Pipeline started on release/1.2.0"
python test_mcp_client.py mcp_jira.server jira_update_issue issue_key=PROJ-123 labels=smart-devops,pipeline fix_version=1.2.0
python test_mcp_client.py mcp_jira.server jira_transition_issue issue_key=PROJ-123 transition_id=21 comment="Build started by SmartDevOps"
python test_mcp_client.py mcp_jira.server jira_create_issue project_key=PROJ issue_type=Task summary="[SmartDevOps] UAT Deployment - my-service 1.2.0" description="Auto-created on SIT success" fix_version=1.2.0
```

#### Jenkins

```bash
python test_mcp_client.py mcp_jenkins.server jenkins_get_job_info job_name=smart-devops/build-app
python test_mcp_client.py mcp_jenkins.server jenkins_get_last_build job_name=smart-devops/build-app
python test_mcp_client.py mcp_jenkins.server jenkins_get_build_status job_name=smart-devops/build-app build_number=42
python test_mcp_client.py mcp_jenkins.server jenkins_get_console_output job_name=smart-devops/build-app build_number=42 start_byte=0
python test_mcp_client.py mcp_jenkins.server jenkins_wait_for_build job_name=smart-devops/build-app build_number=42 poll_interval_seconds=15 timeout_seconds=900
python test_mcp_client.py mcp_jenkins.server jenkins_trigger_build job_name=smart-devops/build-app parameters_json='{"BRANCH":"release/1.2.0","JIRA_TICKET":"PROJ-123"}' wait_for_start=true
```

#### Nexus

```bash
python test_mcp_client.py mcp_nexus.server nexus_list_repositories
python test_mcp_client.py mcp_nexus.server nexus_search_artifacts repository=maven-releases group_id=com.company artifact_id=my-service
python test_mcp_client.py mcp_nexus.server nexus_check_artifact_exists repository=maven-releases artifact_path=com/company/app/my-service/1.2.0/my-service-1.2.0.jar
python test_mcp_client.py mcp_nexus.server nexus_download_artifact repository=maven-releases artifact_path=com/company/app/my-service/1.2.0/my-service-1.2.0.jar output_path=/tmp/my-service-1.2.0.jar
python test_mcp_client.py mcp_nexus.server nexus_upload_raw_artifact repository=raw-hosted directory=/smartdevops-tests/ file_path=/tmp/test-report.txt
python test_mcp_client.py mcp_nexus.server nexus_upload_maven_artifact repository=maven-snapshots group_id=com.company.app artifact_id=my-service version=1.2.0-SNAPSHOT file_path=/path/to/my-service-1.2.0-SNAPSHOT.jar packaging=jar
python test_mcp_client.py mcp_nexus.server nexus_upload_docker_image local_image=my-service:1.2.0 image_tag=1.2.0-PROJ-123
```

#### Bitbucket

```bash
python test_mcp_client.py mcp_bitbucket.server bitbucket_list_open_prs project_key=PROJ repo_slug=my-service
python test_mcp_client.py mcp_bitbucket.server bitbucket_get_pr project_key=PROJ repo_slug=my-service pr_id=42
python test_mcp_client.py mcp_bitbucket.server bitbucket_get_pr_diff project_key=PROJ repo_slug=my-service pr_id=42
python test_mcp_client.py mcp_bitbucket.server bitbucket_get_commit_diff project_key=PROJ repo_slug=my-service since_commit=abc1234 until_commit=def5678
python test_mcp_client.py mcp_bitbucket.server bitbucket_clone_repo project_key=PROJ repo_slug=my-service target_dir=/tmp/my-service-clone branch=release/1.2.0 depth=1
python test_mcp_client.py mcp_bitbucket.server bitbucket_create_tag repo_dir=/tmp/my-service-clone tag_name=v1.2.0 message="Release 1.2.0 - PROJ-123" push=false
python test_mcp_client.py mcp_bitbucket.server bitbucket_push_branch repo_dir=/tmp/my-service-clone branch_name=fix/sonar-PROJ-123
python test_mcp_client.py mcp_bitbucket.server bitbucket_create_pr project_key=PROJ repo_slug=my-service title="[SmartDevOps] Sonar auto-fix for PROJ-123" description="Auto-generated fixes. Review required." source_branch=fix/sonar-PROJ-123 target_branch=develop reviewer_usernames=john.smith,jane.doe
python test_mcp_client.py mcp_bitbucket.server bitbucket_merge_pr project_key=PROJ repo_slug=my-service pr_id=42 merge_strategy=merge-commit
```

#### SonarQube

```bash
python test_mcp_client.py mcp_sonarqube.server sonar_list_projects search=my-service
python test_mcp_client.py mcp_sonarqube.server sonar_get_quality_gate_status project_key=com.company:my-service branch=release/1.2.0
python test_mcp_client.py mcp_sonarqube.server sonar_get_metrics project_key=com.company:my-service branch=release/1.2.0
python test_mcp_client.py mcp_sonarqube.server sonar_get_project_analysis_status project_key=com.company:my-service branch=release/1.2.0
python test_mcp_client.py mcp_sonarqube.server sonar_get_issues project_key=com.company:my-service severities=BLOCKER,CRITICAL types=BUG,VULNERABILITY page_size=20
python test_mcp_client.py mcp_sonarqube.server sonar_get_issue_suggestions project_key=com.company:my-service severities=BLOCKER,CRITICAL
python test_mcp_client.py mcp_sonarqube.server sonar_get_new_code_issues project_key=com.company:my-service branch=release/1.2.0
```

#### Coverity

```bash
python test_mcp_client.py mcp_coverity.server coverity_get_projects
python test_mcp_client.py mcp_coverity.server coverity_get_streams project_name=my-service
python test_mcp_client.py mcp_coverity.server coverity_get_defects project_name=my-service stream_name=my-service-main page_size=20
python test_mcp_client.py mcp_coverity.server coverity_get_defect_details cid=12345 project_name=my-service
python test_mcp_client.py mcp_coverity.server coverity_get_scan_summary project_name=my-service stream_name=my-service-main
python test_mcp_client.py mcp_coverity.server coverity_get_snapshots project_name=my-service stream_name=my-service-main limit=5
```

#### Black Duck

```bash
python test_mcp_client.py mcp_blackduck.server blackduck_list_projects search=my-service
python test_mcp_client.py mcp_blackduck.server blackduck_list_project_versions project_name=my-service
python test_mcp_client.py mcp_blackduck.server blackduck_get_vulnerabilities project_name=my-service version_name=1.2.0 min_cvss_score=7.0
python test_mcp_client.py mcp_blackduck.server blackduck_get_vulnerability_details project_name=my-service version_name=1.2.0 vulnerability_name=CVE-2021-44228
python test_mcp_client.py mcp_blackduck.server blackduck_get_policy_violations project_name=my-service version_name=1.2.0
python test_mcp_client.py mcp_blackduck.server blackduck_get_components project_name=my-service version_name=1.2.0 limit=50
python test_mcp_client.py mcp_blackduck.server blackduck_get_scan_summary project_name=my-service version_name=1.2.0
```

### Run the full test suite

The suite runs all read-only calls against your live instances. State-mutating tools (transitions, PR creation, build triggers) are present in the script but commented out — opt in by editing `test_all_tools.sh`.

```bash
cd agentic-devops-pipeline/mcp_servers

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

./test_all_tools.sh                              # all 7 servers
./test_all_tools.sh jira                         # single server
./test_all_tools.sh coverity blackduck           # multiple servers
./test_all_tools.sh jira jenkins sonarqube       # multiple servers
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
    "jira":      { "command": "python", "args": ["-m", "mcp_jira.server"],      "cwd": "/opt/smart-devops/mcp_servers" },
    "jenkins":   { "command": "python", "args": ["-m", "mcp_jenkins.server"],   "cwd": "/opt/smart-devops/mcp_servers" },
    "nexus":     { "command": "python", "args": ["-m", "mcp_nexus.server"],     "cwd": "/opt/smart-devops/mcp_servers" },
    "bitbucket": { "command": "python", "args": ["-m", "mcp_bitbucket.server"], "cwd": "/opt/smart-devops/mcp_servers" },
    "sonarqube": { "command": "python", "args": ["-m", "mcp_sonarqube.server"], "cwd": "/opt/smart-devops/mcp_servers" },
    "coverity":  { "command": "python", "args": ["-m", "mcp_coverity.server"],  "cwd": "/opt/smart-devops/mcp_servers" },
    "blackduck": { "command": "python", "args": ["-m", "mcp_blackduck.server"], "cwd": "/opt/smart-devops/mcp_servers" }
  }
}
```

No `MCP_TRANSPORT` variable needed — `stdio` is the default.

Update `cwd` to match the actual deployment path on the RHEL VM if different from `/opt/smart-devops`.

---

### SSE mode — for standalone testing or shared team server

The **gateway server** (`mcp_gateway`) aggregates all 7 servers into one FastMCP instance and serves all 47 tools over a single HTTP endpoint. Claude Code connects via URL instead of spawning subprocesses.

**Is a single port recommended?** Yes — one unified server is the standard SSE pattern. It gives you one URL to configure, one port to firewall, and one process to monitor. Per-server separate ports only make sense when you need to give different clients access to different tool subsets, which is not required here.

**Start the gateway:**
```bash
# Foreground (for testing)
MCP_TRANSPORT=sse MCP_PORT=8000 python -m mcp_gateway.server

# Background with nohup
MCP_TRANSPORT=sse MCP_PORT=8000 nohup python -m mcp_gateway.server > /tmp/mcp-gateway.log 2>&1 &

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
mcp dev mcp_servers/mcp_gateway/server.py
# Opens http://localhost:5173 — call any tool interactively
```

**Test the gateway via the CLI test client** (stdio, no HTTP server needed):
```bash
python test_mcp_client.py mcp_gateway.server --list-tools
python test_mcp_client.py mcp_gateway.server jira_get_issue issue_key=PROJ-123
python test_mcp_client.py mcp_gateway.server sonar_get_quality_gate_status project_key=com.company:my-service
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

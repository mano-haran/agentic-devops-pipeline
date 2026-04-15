#!/usr/bin/env bash
# =============================================================================
# Smart DevOps MCP Server — Standalone Tool Test Runner
#
# Run this script from inside the mcp_servers/ directory:
#   cd /opt/smart-devops/mcp_servers
#   chmod +x test_all_tools.sh
#   ./test_all_tools.sh                   # run ALL tests
#   ./test_all_tools.sh jira              # run only Jira tests
#   ./test_all_tools.sh jenkins sonar     # run Jenkins and SonarQube tests
#
# Pre-requisites:
#   1. pip install -e .   (installs the mcp_servers package + dependencies)
#   2. .env file in the parent directory with all credentials
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT="python ${SCRIPT_DIR}/test_mcp_client.py"

# Load .env if present
ENV_FILE="${SCRIPT_DIR}/../.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    echo "[INFO] Loaded environment from $ENV_FILE"
fi

# Colours
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

pass=0; fail=0; skip=0

run_test() {
    local label="$1"; shift
    echo -e "\n${YELLOW}▶ ${label}${NC}"
    if "$@" 2>&1; then
        echo -e "${GREEN}✔ PASS${NC}"
        ((pass++))
    else
        echo -e "${RED}✘ FAIL${NC}"
        ((fail++))
    fi
}

list_tools() {
    local server="$1"
    echo -e "\n${YELLOW}▶ [list-tools] ${server}${NC}"
    $CLIENT "$server" --list-tools
}

summary() {
    echo ""
    echo "=============================="
    echo -e "  PASS: ${GREEN}${pass}${NC}  FAIL: ${RED}${fail}${NC}  SKIP: ${YELLOW}${skip}${NC}"
    echo "=============================="
    [[ $fail -eq 0 ]]
}

# =============================================================================
# JIRA
# =============================================================================
test_jira() {
    echo -e "\n\n${YELLOW}═══════════ JIRA ═══════════${NC}"
    list_tools mcp_jira.server

    local TICKET="${JIRA_TEST_TICKET:-PROJ-1}"

    run_test "jira_get_issue" \
        $CLIENT mcp_jira.server jira_get_issue \
        issue_key="$TICKET"

    run_test "jira_get_issue_status" \
        $CLIENT mcp_jira.server jira_get_issue_status \
        issue_key="$TICKET"

    run_test "jira_get_transitions" \
        $CLIENT mcp_jira.server jira_get_transitions \
        issue_key="$TICKET"

    run_test "jira_add_comment" \
        $CLIENT mcp_jira.server jira_add_comment \
        issue_key="$TICKET" \
        comment="[SmartDevOps] MCP connectivity test - $(date)"

    run_test "jira_get_project_versions" \
        $CLIENT mcp_jira.server jira_get_project_versions \
        project_key="${TICKET%%-*}"

    # Update a safe field (label)
    run_test "jira_update_issue (labels)" \
        $CLIENT mcp_jira.server jira_update_issue \
        issue_key="$TICKET" \
        labels="smart-devops-test"

    # NOTE: jira_transition_issue and jira_create_issue are NOT run automatically
    # as they mutate state. Uncomment and adjust transition_id as needed.
    : <<'MANUAL_TESTS'
    run_test "jira_transition_issue" \
        $CLIENT mcp_jira.server jira_transition_issue \
        issue_key="$TICKET" \
        transition_id="21" \
        comment="Transitioned by SmartDevOps pipeline test"

    run_test "jira_create_issue" \
        $CLIENT mcp_jira.server jira_create_issue \
        project_key="PROJ" \
        issue_type="Task" \
        summary="[TEST] SmartDevOps auto-created ticket" \
        description="Created by MCP test runner"
MANUAL_TESTS
}

# =============================================================================
# JENKINS
# =============================================================================
test_jenkins() {
    echo -e "\n\n${YELLOW}═══════════ JENKINS ═══════════${NC}"
    list_tools mcp_jenkins.server

    local JOB="${JENKINS_TEST_JOB:-smart-devops/build-app}"

    run_test "jenkins_get_job_info" \
        $CLIENT mcp_jenkins.server jenkins_get_job_info \
        job_name="$JOB"

    run_test "jenkins_get_last_build" \
        $CLIENT mcp_jenkins.server jenkins_get_last_build \
        job_name="$JOB"

    # Get last build number for console/status tests
    LAST_BUILD=$($CLIENT mcp_jenkins.server jenkins_get_last_build job_name="$JOB" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('build_number','1'))" 2>/dev/null || echo "1")

    run_test "jenkins_get_build_status" \
        $CLIENT mcp_jenkins.server jenkins_get_build_status \
        job_name="$JOB" \
        build_number="$LAST_BUILD"

    run_test "jenkins_get_console_output" \
        $CLIENT mcp_jenkins.server jenkins_get_console_output \
        job_name="$JOB" \
        build_number="$LAST_BUILD" \
        start_byte=0

    # NOTE: jenkins_trigger_build is NOT run automatically.
    : <<'MANUAL_TESTS'
    run_test "jenkins_trigger_build" \
        $CLIENT mcp_jenkins.server jenkins_trigger_build \
        job_name="$JOB" \
        parameters_json='{"BRANCH":"feature/test","JIRA_TICKET":"PROJ-1"}' \
        wait_for_start=true
MANUAL_TESTS
}

# =============================================================================
# NEXUS
# =============================================================================
test_nexus() {
    echo -e "\n\n${YELLOW}═══════════ NEXUS ═══════════${NC}"
    list_tools mcp_nexus.server

    run_test "nexus_list_repositories" \
        $CLIENT mcp_nexus.server nexus_list_repositories

    local REPO="${NEXUS_TEST_MAVEN_REPO:-maven-releases}"
    run_test "nexus_search_artifacts" \
        $CLIENT mcp_nexus.server nexus_search_artifacts \
        repository="$REPO" \
        group_id="com.company"

    run_test "nexus_check_artifact_exists" \
        $CLIENT mcp_nexus.server nexus_check_artifact_exists \
        repository="$REPO" \
        artifact_path="com/company/app/my-service/1.0.0/my-service-1.0.0.jar"

    # NOTE: Upload tests require actual files. Uncomment with real paths.
    : <<'MANUAL_TESTS'
    # Create a temp test file
    TMPFILE=$(mktemp /tmp/test-artifact-XXXX.txt)
    echo "test artifact content" > "$TMPFILE"

    run_test "nexus_upload_raw_artifact" \
        $CLIENT mcp_nexus.server nexus_upload_raw_artifact \
        repository="raw-hosted" \
        directory="/smartdevops-tests/" \
        file_path="$TMPFILE"

    rm -f "$TMPFILE"

    run_test "nexus_upload_maven_artifact" \
        $CLIENT mcp_nexus.server nexus_upload_maven_artifact \
        repository="maven-snapshots" \
        group_id="com.company.test" \
        artifact_id="mcp-test" \
        version="0.0.1-SNAPSHOT" \
        file_path="/path/to/your/app.jar" \
        packaging="jar"

    run_test "nexus_upload_docker_image" \
        $CLIENT mcp_nexus.server nexus_upload_docker_image \
        local_image="myapp:latest" \
        image_tag="0.0.1-PROJ-1"

    run_test "nexus_download_artifact" \
        $CLIENT mcp_nexus.server nexus_download_artifact \
        repository="maven-releases" \
        artifact_path="com/company/app/my-service/1.0.0/my-service-1.0.0.jar" \
        output_path="/tmp/downloaded-artifact.jar"
MANUAL_TESTS
}

# =============================================================================
# BITBUCKET
# =============================================================================
test_bitbucket() {
    echo -e "\n\n${YELLOW}═══════════ BITBUCKET ═══════════${NC}"
    list_tools mcp_bitbucket.server

    local PROJECT="${BITBUCKET_TEST_PROJECT:-PROJ}"
    local REPO="${BITBUCKET_TEST_REPO:-my-service}"

    run_test "bitbucket_list_open_prs" \
        $CLIENT mcp_bitbucket.server bitbucket_list_open_prs \
        project_key="$PROJECT" \
        repo_slug="$REPO"

    # Clone test — uses a temp directory
    CLONE_DIR=$(mktemp -d /tmp/bb-clone-XXXX)
    run_test "bitbucket_clone_repo" \
        $CLIENT mcp_bitbucket.server bitbucket_clone_repo \
        project_key="$PROJECT" \
        repo_slug="$REPO" \
        target_dir="$CLONE_DIR" \
        branch="develop" \
        depth=1

    # Diff test (requires at least 2 commits)
    run_test "bitbucket_get_pr_diff (PR 1)" \
        $CLIENT mcp_bitbucket.server bitbucket_get_pr_diff \
        project_key="$PROJECT" \
        repo_slug="$REPO" \
        pr_id=1 || true   # PR 1 may not exist; soft-fail

    # Tag test — only runs if clone succeeded
    if [[ -d "$CLONE_DIR/.git" ]]; then
        TAG="test-tag-$(date +%s)"
        run_test "bitbucket_create_tag" \
            $CLIENT mcp_bitbucket.server bitbucket_create_tag \
            repo_dir="$CLONE_DIR" \
            tag_name="$TAG" \
            message="SmartDevOps MCP test tag" \
            push=false   # don't push test tags

        rm -rf "$CLONE_DIR"
    fi

    # NOTE: PR creation/merge mutate state. Uncomment carefully.
    : <<'MANUAL_TESTS'
    run_test "bitbucket_create_pr" \
        $CLIENT mcp_bitbucket.server bitbucket_create_pr \
        project_key="$PROJECT" \
        repo_slug="$REPO" \
        title="[TEST] SmartDevOps MCP test PR" \
        description="Created by MCP test runner" \
        source_branch="feature/mcp-test" \
        target_branch="develop"
MANUAL_TESTS
}

# =============================================================================
# SONARQUBE
# =============================================================================
test_sonarqube() {
    echo -e "\n\n${YELLOW}═══════════ SONARQUBE ═══════════${NC}"
    list_tools mcp_sonarqube.server

    local PROJECT="${SONAR_TEST_PROJECT:-com.company:my-service}"

    run_test "sonar_list_projects" \
        $CLIENT mcp_sonarqube.server sonar_list_projects \
        search="my-service"

    run_test "sonar_get_quality_gate_status" \
        $CLIENT mcp_sonarqube.server sonar_get_quality_gate_status \
        project_key="$PROJECT"

    run_test "sonar_get_metrics" \
        $CLIENT mcp_sonarqube.server sonar_get_metrics \
        project_key="$PROJECT"

    run_test "sonar_get_issues" \
        $CLIENT mcp_sonarqube.server sonar_get_issues \
        project_key="$PROJECT" \
        severities="BLOCKER,CRITICAL" \
        types="BUG,VULNERABILITY" \
        page_size=10

    run_test "sonar_get_issue_suggestions" \
        $CLIENT mcp_sonarqube.server sonar_get_issue_suggestions \
        project_key="$PROJECT" \
        severities="BLOCKER,CRITICAL"

    run_test "sonar_get_new_code_issues" \
        $CLIENT mcp_sonarqube.server sonar_get_new_code_issues \
        project_key="$PROJECT"

    run_test "sonar_get_project_analysis_status" \
        $CLIENT mcp_sonarqube.server sonar_get_project_analysis_status \
        project_key="$PROJECT"
}

# =============================================================================
# COVERITY
# =============================================================================
test_coverity() {
    echo -e "\n\n${YELLOW}═══════════ COVERITY ═══════════${NC}"
    list_tools mcp_coverity.server

    local PROJECT="${COVERITY_TEST_PROJECT:-my-service}"
    local STREAM="${COVERITY_TEST_STREAM:-my-service-main}"

    run_test "coverity_get_projects" \
        $CLIENT mcp_coverity.server coverity_get_projects

    run_test "coverity_get_streams" \
        $CLIENT mcp_coverity.server coverity_get_streams \
        project_name="$PROJECT"

    run_test "coverity_get_defects" \
        $CLIENT mcp_coverity.server coverity_get_defects \
        project_name="$PROJECT" \
        stream_name="$STREAM" \
        page_size=20

    run_test "coverity_get_scan_summary" \
        $CLIENT mcp_coverity.server coverity_get_scan_summary \
        project_name="$PROJECT" \
        stream_name="$STREAM"

    run_test "coverity_get_snapshots" \
        $CLIENT mcp_coverity.server coverity_get_snapshots \
        project_name="$PROJECT" \
        stream_name="$STREAM" \
        limit=3

    # CID test — adjust to a real CID from your Coverity instance
    : <<'MANUAL_TESTS'
    run_test "coverity_get_defect_details" \
        $CLIENT mcp_coverity.server coverity_get_defect_details \
        cid="12345" \
        project_name="$PROJECT"
MANUAL_TESTS
}

# =============================================================================
# BLACKDUCK
# =============================================================================
test_blackduck() {
    echo -e "\n\n${YELLOW}═══════════ BLACKDUCK ═══════════${NC}"
    list_tools mcp_blackduck.server

    local PROJECT="${BLACKDUCK_TEST_PROJECT:-my-service}"
    local VERSION="${BLACKDUCK_TEST_VERSION:-1.0.0}"

    run_test "blackduck_list_projects" \
        $CLIENT mcp_blackduck.server blackduck_list_projects \
        search="my-service"

    run_test "blackduck_list_project_versions" \
        $CLIENT mcp_blackduck.server blackduck_list_project_versions \
        project_name="$PROJECT"

    run_test "blackduck_get_vulnerabilities" \
        $CLIENT mcp_blackduck.server blackduck_get_vulnerabilities \
        project_name="$PROJECT" \
        version_name="$VERSION" \
        min_cvss_score=7.0

    run_test "blackduck_get_policy_violations" \
        $CLIENT mcp_blackduck.server blackduck_get_policy_violations \
        project_name="$PROJECT" \
        version_name="$VERSION"

    run_test "blackduck_get_components" \
        $CLIENT mcp_blackduck.server blackduck_get_components \
        project_name="$PROJECT" \
        version_name="$VERSION" \
        limit=20

    run_test "blackduck_get_scan_summary" \
        $CLIENT mcp_blackduck.server blackduck_get_scan_summary \
        project_name="$PROJECT" \
        version_name="$VERSION"

    # Specific CVE lookup — requires a known CVE name
    : <<'MANUAL_TESTS'
    run_test "blackduck_get_vulnerability_details" \
        $CLIENT mcp_blackduck.server blackduck_get_vulnerability_details \
        project_name="$PROJECT" \
        version_name="$VERSION" \
        vulnerability_name="CVE-2021-44228"
MANUAL_TESTS
}

# =============================================================================
# Main dispatch
# =============================================================================

SUITES=("$@")
if [[ ${#SUITES[@]} -eq 0 ]]; then
    SUITES=("jira" "jenkins" "nexus" "bitbucket" "sonarqube" "coverity" "blackduck")
fi

for suite in "${SUITES[@]}"; do
    case "$suite" in
        jira)       test_jira ;;
        jenkins)    test_jenkins ;;
        nexus)      test_nexus ;;
        bitbucket)  test_bitbucket ;;
        sonar*)     test_sonarqube ;;
        coverity)   test_coverity ;;
        blackduck)  test_blackduck ;;
        *)
            echo "Unknown suite: $suite"
            echo "Valid: jira jenkins nexus bitbucket sonarqube coverity blackduck"
            ((fail++))
            ;;
    esac
done

summary

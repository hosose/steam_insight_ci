#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${ROOT_DIR}/infra"
TARGET_REPOSITORY="${1:-}"

for command_name in terraform gh aws python3; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] 필수 명령을 찾을 수 없습니다: ${command_name}" >&2
    exit 1
  fi
done

if ! gh auth status >/dev/null 2>&1; then
  echo "[ERROR] GitHub CLI 로그인이 필요합니다: gh auth login" >&2
  exit 1
fi

if [[ -z "${TARGET_REPOSITORY}" ]]; then
  TARGET_REPOSITORY="$(cd "${ROOT_DIR}" && gh repo view --json nameWithOwner --jq '.nameWithOwner')"
fi

python3 "${ROOT_DIR}/scripts/ci/verify_ci_config.py" --root "${ROOT_DIR}" --repo "${TARGET_REPOSITORY}"

AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region)"
AWS_ROLE_ARN="$(terraform -chdir="${INFRA_DIR}" output -raw github_actions_ci_role_arn)"
ECR_WEB_REPOSITORY="$(terraform -chdir="${INFRA_DIR}" output -raw web_ecr_repository_name)"
ECR_WAS_REPOSITORY="$(terraform -chdir="${INFRA_DIR}" output -raw was_ecr_repository_name)"

if [[ -z "${AWS_ROLE_ARN}" || "${AWS_ROLE_ARN}" == "null" ]]; then
  echo "[ERROR] GitHub Actions CI Role Terraform Output이 없습니다." >&2
  echo "        enable_github_actions_ci=true로 Terraform Apply가 완료되었는지 확인하세요." >&2
  exit 1
fi

check_variable() {
  local variable_name="$1"
  local expected_value="$2"
  local actual_value

  actual_value="$(gh variable get "${variable_name}" --repo "${TARGET_REPOSITORY}" 2>/dev/null || true)"
  if [[ -z "${actual_value}" ]]; then
    echo "[ERROR] GitHub Repository variable이 없습니다: ${variable_name}" >&2
    return 1
  fi
  if [[ "${actual_value}" != "${expected_value}" ]]; then
    echo "[ERROR] GitHub Repository variable 값이 Terraform Output과 다릅니다: ${variable_name}" >&2
    echo "        GitHub : ${actual_value}" >&2
    echo "        Terraform: ${expected_value}" >&2
    return 1
  fi
  echo "[OK] GitHub variable ${variable_name}"
}

check_variable AWS_REGION "${AWS_REGION}"
check_variable AWS_ROLE_ARN "${AWS_ROLE_ARN}"
check_variable ECR_WEB_REPOSITORY "${ECR_WEB_REPOSITORY}"
check_variable ECR_WAS_REPOSITORY "${ECR_WAS_REPOSITORY}"

ROLE_NAME="${AWS_ROLE_ARN##*/}"
aws iam get-role --role-name "${ROLE_NAME}" >/dev/null
echo "[OK] AWS IAM Role: ${ROLE_NAME}"

aws ecr describe-repositories --region "${AWS_REGION}" --repository-names "${ECR_WEB_REPOSITORY}" "${ECR_WAS_REPOSITORY}" >/dev/null
echo "[OK] ECR repositories: ${ECR_WEB_REPOSITORY}, ${ECR_WAS_REPOSITORY}"

if gh workflow view ci.yml --repo "${TARGET_REPOSITORY}" >/dev/null 2>&1; then
  echo "[OK] GitHub Workflow 등록 확인: ci.yml"
else
  echo "[WARN] GitHub에서 ci.yml을 아직 찾지 못했습니다. 최초 git push 후 다시 확인하세요."
fi

echo
echo "CI 원격 설정 확인 완료: ${TARGET_REPOSITORY}"

#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${ROOT_DIR}/infra"
TARGET_REPOSITORY="${1:-}"

required_commands=(terraform gh)
for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] 필수 명령을 찾을 수 없습니다: ${command_name}" >&2
    exit 1
  fi
done

if ! gh auth status >/dev/null 2>&1; then
  echo "[ERROR] gh auth login을 먼저 실행하세요." >&2
  exit 1
fi

if [[ -z "${TARGET_REPOSITORY}" ]]; then
  TARGET_REPOSITORY="$(cd "${ROOT_DIR}" && gh repo view --json nameWithOwner --jq '.nameWithOwner')"
fi

AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region)"
AWS_ROLE_ARN="$(terraform -chdir="${INFRA_DIR}" output -raw github_actions_ci_role_arn)"
ECR_WEB_REPOSITORY="$(terraform -chdir="${INFRA_DIR}" output -raw web_ecr_repository_name)"
ECR_WAS_REPOSITORY="$(terraform -chdir="${INFRA_DIR}" output -raw was_ecr_repository_name)"

if [[ -z "${AWS_ROLE_ARN}" || "${AWS_ROLE_ARN}" == "null" ]]; then
  echo "[ERROR] GitHub Actions CI Role이 생성되지 않았습니다." >&2
  echo "        terraform.tfvars에서 enable_github_actions_ci=true인지 확인하세요." >&2
  exit 1
fi

gh variable set AWS_REGION --repo "${TARGET_REPOSITORY}" --body "${AWS_REGION}"
gh variable set AWS_ROLE_ARN --repo "${TARGET_REPOSITORY}" --body "${AWS_ROLE_ARN}"
gh variable set ECR_WEB_REPOSITORY --repo "${TARGET_REPOSITORY}" --body "${ECR_WEB_REPOSITORY}"
gh variable set ECR_WAS_REPOSITORY --repo "${TARGET_REPOSITORY}" --body "${ECR_WAS_REPOSITORY}"

echo
echo "GitHub Repository variables 등록 완료: ${TARGET_REPOSITORY}"
gh variable list --repo "${TARGET_REPOSITORY}"

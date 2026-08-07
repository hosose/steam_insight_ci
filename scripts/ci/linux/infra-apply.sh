#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${ROOT_DIR}/infra"
AUTO_APPROVE="false"
[[ "${1:-}" == "--auto-approve" || "${1:-}" == "-auto-approve" ]] && AUTO_APPROVE="true"
PLAN_FILE="$(mktemp "${TMPDIR:-/tmp}/tf-k8s-ci.XXXXXX")"
trap 'rm -f "${PLAN_FILE}"' EXIT

for command_name in terraform aws; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] 필수 명령을 찾을 수 없습니다: ${command_name}" >&2
    exit 1
  fi
done

if [[ ! -f "${INFRA_DIR}/terraform.tfvars" ]]; then
  echo "[ERROR] ${INFRA_DIR}/terraform.tfvars 파일이 없습니다." >&2
  echo "        terraform.tfvars.example을 복사한 뒤 값을 확인하세요." >&2
  exit 1
fi

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "[ERROR] AWS CLI 인증이 필요합니다." >&2
  exit 1
fi

echo "[1/5] Terraform 형식 정리"
terraform -chdir="${INFRA_DIR}" fmt -recursive

echo "[2/5] Terraform 초기화"
terraform -chdir="${INFRA_DIR}" init

echo "[3/5] Terraform 검증"
terraform -chdir="${INFRA_DIR}" validate

echo "[4/5] Terraform Plan 생성"
terraform -chdir="${INFRA_DIR}" plan -out="${PLAN_FILE}"

if [[ "${AUTO_APPROVE}" != "true" ]]; then
  read -r -p "위 Terraform Plan을 적용하시겠습니까? [y/N]: " answer
  [[ "${answer}" =~ ^[Yy]$ ]] || { echo "Terraform Apply를 취소했습니다."; exit 0; }
fi

echo "[5/5] Terraform 적용"
terraform -chdir="${INFRA_DIR}" apply "${PLAN_FILE}"

echo
echo "GitHub Actions CI용 AWS 리소스 반영 완료"
terraform -chdir="${INFRA_DIR}" output -raw github_actions_ci_role_arn
echo

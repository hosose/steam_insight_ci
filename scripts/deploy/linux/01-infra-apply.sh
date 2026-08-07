#!/usr/bin/env bash
set -Eeuo pipefail

# 책임: Terraform 인프라 생성 + 로컬 kubectl 연결까지만 수행한다.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${PROJECT_ROOT}/infra"

required_commands=(terraform aws kubectl)
for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] 필수 명령을 찾을 수 없습니다: ${command_name}" >&2
    exit 1
  fi
done

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "[ERROR] AWS CLI 인증이 필요합니다." >&2
  exit 1
fi

if [[ ! -f "${INFRA_DIR}/terraform.tfvars" ]]; then
  echo "[ERROR] ${INFRA_DIR}/terraform.tfvars 파일이 없습니다." >&2
  echo "       terraform.tfvars.example을 복사한 뒤 값을 확인하세요." >&2
  exit 1
fi

echo "[1/3] Terraform init"
terraform -chdir="${INFRA_DIR}" init

echo "[2/3] Terraform apply"
terraform -chdir="${INFRA_DIR}" apply -auto-approve

AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region)"
CLUSTER_NAME="$(terraform -chdir="${INFRA_DIR}" output -raw cluster_name)"

echo "[3/3] kubeconfig 갱신 및 Metrics Server 확인"
aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}"
kubectl rollout status deployment/metrics-server -n kube-system --timeout=15m

echo
printf '인프라 준비 완료\nCluster: %s\nRegion : %s\n' "${CLUSTER_NAME}" "${AWS_REGION}"
echo "다음 단계: ./project.sh image"

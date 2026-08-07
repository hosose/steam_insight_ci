#!/usr/bin/env bash
set -Eeuo pipefail

# 책임: GitOps Repository에 있는 현재 Manifest를 수동으로 배포한다.
# 향후 이 kubectl apply 역할은 Argo CD가 대신한다.
SOURCE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${SOURCE_REPO_ROOT}/infra"
DEFAULT_GITOPS_REPO_DIR="$(cd "${SOURCE_REPO_ROOT}/.." && pwd)/tf-k8s-cd"
GITOPS_REPO_DIR="${GITOPS_REPO_DIR:-${DEFAULT_GITOPS_REPO_DIR}}"
GITOPS_MANIFEST_TEMPLATE="${GITOPS_MANIFEST_TEMPLATE:-${GITOPS_REPO_DIR}/apps/de-ai-07/legacy/app.yaml.tpl}"
RENDERER="${SOURCE_REPO_ROOT}/tools/render_manifest.py"
APP_NAMESPACE="${APP_NAMESPACE:-de-ai-07}"
IMAGE_TAG="${IMAGE_TAG:-k8s-auto}"

required_commands=(terraform aws kubectl python3)
for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] 필수 명령을 찾을 수 없습니다: ${command_name}" >&2
    exit 1
  fi
done

if [[ ! -f "${GITOPS_MANIFEST_TEMPLATE}" ]]; then
  echo "[ERROR] GitOps Manifest를 찾을 수 없습니다." >&2
  echo "        ${GITOPS_MANIFEST_TEMPLATE}" >&2
  echo "        두 Repository를 같은 상위 폴더에 배치하거나" >&2
  echo "        GITOPS_REPO_DIR 환경변수를 지정하세요." >&2
  exit 1
fi

AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region)"
CLUSTER_NAME="$(terraform -chdir="${INFRA_DIR}" output -raw cluster_name)"
WEB_REPO="$(terraform -chdir="${INFRA_DIR}" output -raw web_ecr_repository_url)"
WAS_REPO="$(terraform -chdir="${INFRA_DIR}" output -raw was_ecr_repository_url)"

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}" >/dev/null

export APP_NAMESPACE
export WEB_IMAGE="${WEB_IMAGE:-${WEB_REPO}:${IMAGE_TAG}}"
export WAS_IMAGE="${WAS_IMAGE:-${WAS_REPO}:${IMAGE_TAG}}"

python3 "${RENDERER}" --template "${GITOPS_MANIFEST_TEMPLATE}" | kubectl apply -f -

kubectl rollout status deployment/was -n "${APP_NAMESPACE}" --timeout=20m
kubectl rollout status deployment/web -n "${APP_NAMESPACE}" --timeout=20m

echo
kubectl get pods,svc,ingress,hpa,pdb -n "${APP_NAMESPACE}"
echo
echo "Kubernetes 수동 배포 완료"
echo "GitOps Repository: ${GITOPS_REPO_DIR}"
echo "WEB_IMAGE=${WEB_IMAGE}"
echo "WAS_IMAGE=${WAS_IMAGE}"

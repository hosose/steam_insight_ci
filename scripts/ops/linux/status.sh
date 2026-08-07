#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${PROJECT_ROOT}/infra"
APP_NAMESPACE="${APP_NAMESPACE:-de-ai-07}"

AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region)"
CLUSTER_NAME="$(terraform -chdir="${INFRA_DIR}" output -raw cluster_name)"
aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}" >/dev/null

echo "=== EKS Auto Mode 리소스 ==="
kubectl get nodepool,nodeclass,nodeclaim,nodes
echo
echo "=== 애플리케이션 리소스 ==="
kubectl get pods,svc,ingress,hpa,pdb -n "${APP_NAMESPACE}"
echo
echo "=== Pod 배치 정보 ==="
kubectl get pods -n "${APP_NAMESPACE}" -o wide

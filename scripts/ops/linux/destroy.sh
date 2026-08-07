#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${PROJECT_ROOT}/infra"
APP_NAMESPACE="${APP_NAMESPACE:-de-ai-07}"
AWS_REGION=""
CLUSTER_NAME=""

if command -v terraform >/dev/null 2>&1; then
  AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region 2>/dev/null || true)"
  CLUSTER_NAME="$(terraform -chdir="${INFRA_DIR}" output -raw cluster_name 2>/dev/null || true)"
fi

if [[ -n "${AWS_REGION}" && -n "${CLUSTER_NAME}" ]] && command -v aws >/dev/null 2>&1; then
  aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
fi

# Ingress를 먼저 삭제해 EKS Auto Mode가 ALB 관련 리소스를 정리하도록 한다.
if command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
  ALB_HOST="$(kubectl get ingress public-alb -n "${APP_NAMESPACE}" \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  ALB_ARN=""

  if [[ -n "${ALB_HOST}" && -n "${AWS_REGION}" ]] && command -v aws >/dev/null 2>&1; then
    ALB_ARN="$(aws elbv2 describe-load-balancers \
      --region "${AWS_REGION}" \
      --query "LoadBalancers[?DNSName=='${ALB_HOST}'].LoadBalancerArn | [0]" \
      --output text 2>/dev/null || true)"
    [[ "${ALB_ARN}" == "None" ]] && ALB_ARN=""
  fi

  kubectl delete ingress public-alb -n "${APP_NAMESPACE}" --ignore-not-found=true --wait=true || true

  if [[ -n "${ALB_ARN}" ]]; then
    aws elbv2 wait load-balancers-deleted \
      --region "${AWS_REGION}" \
      --load-balancer-arns "${ALB_ARN}" || true
  fi

  kubectl delete namespace "${APP_NAMESPACE}" --ignore-not-found=true --wait=true || true
  kubectl delete ingressclass alb --ignore-not-found=true || true
  kubectl delete ingressclassparams alb --ignore-not-found=true || true
fi

terraform -chdir="${INFRA_DIR}" destroy -auto-approve

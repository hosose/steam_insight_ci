#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMAND="${1:-help}"
[[ $# -gt 0 ]] && shift || true

usage() {
  cat <<'USAGE'
사용법: ./project.sh <명령> [옵션]

최초 구축/배포
  deploy                  인프라 생성 → 이미지 Push → Secret → Kubernetes 배포
  infra                   Terraform 인프라만 생성하고 kubeconfig 연결
  image                   WEB/WAS 이미지를 빌드하여 ECR Push
  secret                  RDS 정보를 Kubernetes Secret으로 동기화
  k8s                     tf-k8s-cd Manifest를 수동 적용

운영
  status                  EKS 및 애플리케이션 상태 확인
  destroy                 Kubernetes 리소스와 Terraform 인프라 삭제

CI
  ci-setup [OWNER/REPO]   OIDC/IAM 반영 → GitHub 변수 등록 → 로컬 검증
  ci-validate             Terraform/Docker 로컬 CI 검증만 수행
  ci-check [OWNER/REPO]   GitHub 변수, IAM Role, ECR, Workflow 연결 확인

예시
  IMAGE_TAG=k8s-auto ./project.sh deploy
  ./project.sh ci-setup GitHub_ID/tf-k8s-ci
  ./project.sh ci-check GitHub_ID/tf-k8s-ci
  ./project.sh status
  ./project.sh destroy
USAGE
}

case "${COMMAND}" in
  deploy)
    exec "${PROJECT_ROOT}/scripts/deploy/linux/deploy-all.sh" "$@"
    ;;
  infra)
    exec "${PROJECT_ROOT}/scripts/deploy/linux/01-infra-apply.sh" "$@"
    ;;
  image)
    exec "${PROJECT_ROOT}/scripts/deploy/linux/02-image-build-push.sh" "$@"
    ;;
  secret)
    exec "${PROJECT_ROOT}/scripts/deploy/linux/03-secret-sync.sh" "$@"
    ;;
  k8s)
    exec "${PROJECT_ROOT}/scripts/deploy/linux/04-k8s-deploy.sh" "$@"
    ;;
  status)
    exec "${PROJECT_ROOT}/scripts/ops/linux/status.sh" "$@"
    ;;
  destroy)
    exec "${PROJECT_ROOT}/scripts/ops/linux/destroy.sh" "$@"
    ;;
  ci-setup)
    exec "${PROJECT_ROOT}/scripts/ci/linux/setup-all.sh" "$@"
    ;;
  ci-validate)
    exec "${PROJECT_ROOT}/scripts/ci/linux/validate.sh" "$@"
    ;;
  ci-check)
    exec "${PROJECT_ROOT}/scripts/ci/linux/verify-github.sh" "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "[ERROR] 알 수 없는 명령: ${COMMAND}" >&2
    echo >&2
    usage >&2
    exit 1
    ;;
esac

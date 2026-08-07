#!/usr/bin/env bash
set -Eeuo pipefail

# 호환용 통합 실행기: Source Repository의 1~3단계와 GitOps Manifest를 사용하는 4단계를 순서대로 호출한다.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export APP_NAMESPACE="${APP_NAMESPACE:-de-ai-07}"
export IMAGE_TAG="${IMAGE_TAG:-k8s-auto}"

"${PROJECT_ROOT}/scripts/deploy/linux/01-infra-apply.sh"
"${PROJECT_ROOT}/scripts/deploy/linux/02-image-build-push.sh"
"${PROJECT_ROOT}/scripts/deploy/linux/03-secret-sync.sh"
"${PROJECT_ROOT}/scripts/deploy/linux/04-k8s-deploy.sh"

echo
echo "전체 수동 배포 완료"
echo "Namespace: ${APP_NAMESPACE}"
echo "Image Tag: ${IMAGE_TAG}"

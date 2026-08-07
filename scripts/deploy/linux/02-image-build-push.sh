#!/usr/bin/env bash
set -Eeuo pipefail

# 책임: WEB/WAS 컨테이너 이미지를 빌드하고 ECR에 Push한다.
# 향후 이 파일의 역할은 GitHub Actions CI Workflow로 이전한다.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${PROJECT_ROOT}/infra"
IMAGE_TAG="${IMAGE_TAG:-k8s-auto}"

required_commands=(terraform aws docker)
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

if ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker daemon이 실행 중이 아닙니다." >&2
  exit 1
fi

AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region)"
WEB_REPO="$(terraform -chdir="${INFRA_DIR}" output -raw web_ecr_repository_url)"
WAS_REPO="$(terraform -chdir="${INFRA_DIR}" output -raw was_ecr_repository_url)"
ECR_REGISTRY="${WEB_REPO%%/*}"

echo "[1/3] ECR 로그인"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "[2/3] WEB 이미지 빌드 및 Push: ${WEB_REPO}:${IMAGE_TAG}"
docker build --platform linux/amd64 -t "${WEB_REPO}:${IMAGE_TAG}" "${PROJECT_ROOT}/apps/web"
docker push "${WEB_REPO}:${IMAGE_TAG}"

echo "[3/3] WAS 이미지 빌드 및 Push: ${WAS_REPO}:${IMAGE_TAG}"
docker build --platform linux/amd64 -t "${WAS_REPO}:${IMAGE_TAG}" "${PROJECT_ROOT}/apps/was"
docker push "${WAS_REPO}:${IMAGE_TAG}"

echo
echo "이미지 Push 완료"
echo "WEB_IMAGE=${WEB_REPO}:${IMAGE_TAG}"
echo "WAS_IMAGE=${WAS_REPO}:${IMAGE_TAG}"
echo "다음 단계도 동일한 IMAGE_TAG=${IMAGE_TAG} 값을 사용해야 합니다."
echo "다음 단계: ./project.sh secret"

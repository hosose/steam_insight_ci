#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${ROOT_DIR}/infra"
WEB_IMAGE="tf-k8s-web:ci-test"
WAS_IMAGE="tf-k8s-was:ci-test"
WEB_CONTAINER="tf-k8s-web-ci-${RANDOM}"
WAS_CONTAINER="tf-k8s-was-ci-${RANDOM}"

for command_name in terraform docker curl python3; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] 필수 명령을 찾을 수 없습니다: ${command_name}" >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker Engine 또는 Docker Desktop이 실행 중이 아닙니다." >&2
  exit 1
fi

cleanup() {
  docker rm -f "${WEB_CONTAINER}" "${WAS_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for_url() {
  local url="$1"
  local name="$2"

  for _ in $(seq 1 30); do
    if curl --fail --silent --show-error "${url}" >/dev/null; then
      echo "[OK] ${name} health check"
      return 0
    fi
    sleep 1
  done

  echo "[ERROR] ${name} health check failed: ${url}" >&2
  return 1
}

echo "[1/7] CI 필수 파일과 terraform.tfvars 설정 검사"
python3 "${ROOT_DIR}/scripts/ci/verify_ci_config.py" --root "${ROOT_DIR}"

echo "[2/7] Terraform format 검사"
terraform -chdir="${INFRA_DIR}" fmt -check -recursive

echo "[3/7] Terraform 초기화"
terraform -chdir="${INFRA_DIR}" init -backend=false -input=false

echo "[4/7] Terraform 문법 및 참조 검사"
terraform -chdir="${INFRA_DIR}" validate

echo "[5/7] WAS Python 문법 검사"
python3 -m py_compile "${ROOT_DIR}/apps/was/app.py"

echo "[6/7] WEB 이미지 빌드 및 상태 확인"
docker build --platform linux/amd64 --tag "${WEB_IMAGE}" "${ROOT_DIR}/apps/web"
docker run --detach --name "${WEB_CONTAINER}" --add-host was-service:127.0.0.1 --publish 18080:80 "${WEB_IMAGE}" >/dev/null
wait_for_url "http://127.0.0.1:18080/health" "WEB"

echo "[7/7] WAS 이미지 빌드 및 상태 확인"
docker build --platform linux/amd64 --tag "${WAS_IMAGE}" "${ROOT_DIR}/apps/was"
docker run --detach --name "${WAS_CONTAINER}" --publish 18000:8000 "${WAS_IMAGE}" >/dev/null
wait_for_url "http://127.0.0.1:18000/health" "WAS"

echo
printf '%s\n' "CI validation completed successfully."

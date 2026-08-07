#!/usr/bin/env bash
set -Eeuo pipefail

# 현재 파일 위치:
# scripts/ci/linux/setup-all.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 프로젝트 루트:
# scripts/ci/linux → 프로젝트 루트까지 ../../..
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

TARGET_REPOSITORY=""
AUTO_APPROVE="false"

usage() {
  cat <<'USAGE'
사용법:
  ./setup-ci.sh [OWNER/REPO] [--auto-approve]
  ./project.sh ci-setup [OWNER/REPO] [--auto-approve]

예시:
  ./setup-ci.sh GitHub_ID/tf-k8s-ci
  ./setup-ci.sh GitHub_ID/tf-k8s-ci --auto-approve
USAGE
}

# 전달받은 명령행 인수를 분석한다.
for argument in "$@"; do
  case "${argument}" in
    --auto-approve|-auto-approve)
      AUTO_APPROVE="true"
      ;;

    -h|--help)
      usage
      exit 0
      ;;

    -*)
      echo "[ERROR] 지원하지 않는 옵션입니다: ${argument}" >&2
      usage >&2
      exit 1
      ;;

    *)
      if [[ -n "${TARGET_REPOSITORY}" ]]; then
        echo "[ERROR] GitHub Repository는 하나만 지정할 수 있습니다." >&2
        usage >&2
        exit 1
      fi

      TARGET_REPOSITORY="${argument}"
      ;;
  esac
done

echo
echo "============================================================"
echo "[1/5] CI 사전 설정을 검사합니다."
echo "============================================================"

# VERIFY_ARGS에는 처음부터 값이 들어가므로
# macOS Bash 3.2의 빈 배열 문제가 발생하지 않는다.
VERIFY_ARGS=(
  --root "${ROOT_DIR}"
)

if [[ -n "${TARGET_REPOSITORY}" ]]; then
  VERIFY_ARGS+=(
    --repo "${TARGET_REPOSITORY}"
  )
fi

python3 \
  "${ROOT_DIR}/scripts/ci/verify_ci_config.py" \
  "${VERIFY_ARGS[@]}"

echo
echo "============================================================"
echo "[2/5] GitHub Actions CI용 AWS 인프라를 반영합니다."
echo "============================================================"

# macOS 기본 Bash 3.2에서는 set -u 상태에서
# 빈 배열을 참조하면 unbound variable 오류가 발생할 수 있다.
# 따라서 배열을 사용하지 않고 조건에 따라 직접 실행한다.
if [[ "${AUTO_APPROVE}" == "true" ]]; then
  "${SCRIPT_DIR}/infra-apply.sh" --auto-approve
else
  "${SCRIPT_DIR}/infra-apply.sh"
fi

echo
echo "============================================================"
echo "[3/5] GitHub Repository Variables를 등록합니다."
echo "============================================================"

if [[ -n "${TARGET_REPOSITORY}" ]]; then
  "${SCRIPT_DIR}/configure-github-variables.sh" \
    "${TARGET_REPOSITORY}"
else
  "${SCRIPT_DIR}/configure-github-variables.sh"
fi

echo
echo "============================================================"
echo "[4/5] 로컬 CI 구성을 검증합니다."
echo "============================================================"

"${SCRIPT_DIR}/validate.sh"

echo
echo "============================================================"
echo "[5/5] AWS와 GitHub CI 연결 상태를 확인합니다."
echo "============================================================"

if [[ -n "${TARGET_REPOSITORY}" ]]; then
  "${SCRIPT_DIR}/verify-github.sh" \
    "${TARGET_REPOSITORY}"
else
  "${SCRIPT_DIR}/verify-github.sh"
fi

echo
echo "============================================================"
echo "CI 준비 전체 완료"
echo "============================================================"
echo
echo "다음 명령으로 GitHub에 반영하세요."
echo
echo 'git add .'
echo 'git commit -m "feat: add GitHub Actions CI with AWS OIDC"'
echo 'git push origin main'
echo
#!/usr/bin/env bash
set -Eeuo pipefail

# 책임: Secrets Manager의 RDS 접속 정보를 Kubernetes Secret으로 동기화한다.
# 비밀번호는 Git 또는 Manifest 파일에 기록하지 않는다.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_DIR="${PROJECT_ROOT}/infra"
APP_NAMESPACE="${APP_NAMESPACE:-de-ai-07}"

required_commands=(terraform aws kubectl python3)
for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] 필수 명령을 찾을 수 없습니다: ${command_name}" >&2
    exit 1
  fi
done

AWS_REGION="$(terraform -chdir="${INFRA_DIR}" output -raw aws_region)"
CLUSTER_NAME="$(terraform -chdir="${INFRA_DIR}" output -raw cluster_name)"
RDS_HOST="$(terraform -chdir="${INFRA_DIR}" output -raw rds_endpoint)"
RDS_PORT="$(terraform -chdir="${INFRA_DIR}" output -raw rds_port)"
RDS_DB_NAME="$(terraform -chdir="${INFRA_DIR}" output -raw rds_db_name)"
RDS_SECRET_ARN="$(terraform -chdir="${INFRA_DIR}" output -raw rds_master_secret_arn)"

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}" >/dev/null

SECRET_JSON="$(aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "${RDS_SECRET_ARN}" \
  --query SecretString \
  --output text)"

DB_USER="$(SECRET_JSON="${SECRET_JSON}" python3 -c 'import json,os; print(json.loads(os.environ["SECRET_JSON"])["username"])')"
DB_PASSWORD="$(SECRET_JSON="${SECRET_JSON}" python3 -c 'import json,os; print(json.loads(os.environ["SECRET_JSON"])["password"])')"
unset SECRET_JSON

kubectl create namespace "${APP_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic rds-secret \
  --namespace "${APP_NAMESPACE}" \
  --from-literal="DB_HOST=${RDS_HOST}" \
  --from-literal="DB_PORT=${RDS_PORT}" \
  --from-literal="DB_NAME=${RDS_DB_NAME}" \
  --from-literal="DB_USER=${DB_USER}" \
  --from-literal="DB_PASSWORD=${DB_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

unset DB_USER DB_PASSWORD

echo "RDS Secret 동기화 완료: ${APP_NAMESPACE}/rds-secret"
echo "다음 단계: ./project.sh k8s"

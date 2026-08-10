@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
set "INFRA_DIR=%PROJECT_ROOT%\infra"
if not defined APP_NAMESPACE set "APP_NAMESPACE=steam-insight"
if not defined PYTHON_CMD set "PYTHON_CMD=python"

set "SECRET_JSON_FILE=%TEMP%\steam-insight-rds-secret-%RANDOM%.json"
set "DB_ENV_FILE=%TEMP%\steam-insight-rds-env-%RANDOM%.txt"

call :terraform_output AWS_REGION aws_region || goto :error
call :terraform_output CLUSTER_NAME cluster_name || goto :error
call :terraform_output RDS_HOST rds_endpoint || goto :error
call :terraform_output RDS_PORT rds_port || goto :error
call :terraform_output RDS_DB_NAME rds_db_name || goto :error
call :terraform_output RDS_SECRET_ARN rds_master_secret_arn || goto :error

aws eks update-kubeconfig --region "%AWS_REGION%" --name "%CLUSTER_NAME%" || goto :error
aws secretsmanager get-secret-value ^
  --region "%AWS_REGION%" ^
  --secret-id "%RDS_SECRET_ARN%" ^
  --query SecretString ^
  --output text > "%SECRET_JSON_FILE%"
if errorlevel 1 goto :error

%PYTHON_CMD% -c "import json, os, pathlib; d=json.loads(pathlib.Path(r'%SECRET_JSON_FILE%').read_text(encoding='utf-8')); lines=['DB_HOST=%RDS_HOST%','DB_PORT=%RDS_PORT%','DB_NAME=%RDS_DB_NAME%','DB_USER='+d['username'],'DB_PASSWORD='+d['password']]; env_files=[os.path.join(r'%PROJECT_ROOT%', '.env'), os.path.join(r'%PROJECT_ROOT%', 'Steam_Insight_Dashboard', '.env')]; [lines.append(f'{k}={v}') for ef in env_files if os.path.exists(ef) for line in pathlib.Path(ef).read_text(encoding='utf-8').splitlines() if '=' in line and not line.strip().startswith('#') for k, v in [line.strip().split('=', 1)] for k, v in [(k.strip(), v.strip().strip('\"').strip('\''))] if k in ['STEAM_API_KEY', 'Bedrock_API_Key', 'BEDROCK_API_KEY'] and v]; pathlib.Path(r'%DB_ENV_FILE%').write_text('\n'.join(lines)+'\n', encoding='utf-8')"
if errorlevel 1 goto :error

kubectl create namespace "%APP_NAMESPACE%" --dry-run=client -o yaml | kubectl apply -f -
if errorlevel 1 goto :error
kubectl create secret generic rds-secret ^
  --namespace "%APP_NAMESPACE%" ^
  --from-env-file="%DB_ENV_FILE%" ^
  --dry-run=client -o yaml | kubectl apply -f -
if errorlevel 1 goto :error

call :cleanup
echo RDS Secret 동기화 완료: %APP_NAMESPACE%/rds-secret
exit /b 0

:terraform_output
set "%~1="
pushd "%INFRA_DIR%" >nul
for /f "usebackq delims=" %%A in (`terraform output -raw %~2`) do set "%~1=%%A"
popd >nul
if not defined %~1 exit /b 1
exit /b 0

:cleanup
del /q "%SECRET_JSON_FILE%" >nul 2>&1
del /q "%DB_ENV_FILE%" >nul 2>&1
exit /b 0

:error
call :cleanup
echo [ERROR] RDS Secret 동기화 중 오류가 발생했습니다.
exit /b 1

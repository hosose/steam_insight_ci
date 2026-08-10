@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "ROOT_DIR=%%~fI"
set "INFRA_DIR=%ROOT_DIR%\infra"
set "WEB_IMAGE=tf-k8s-web:ci-test"
set "WAS_IMAGE=tf-k8s-was:ci-test"
set "WEB_CONTAINER=tf-k8s-web-ci-%RANDOM%"
set "WAS_CONTAINER=tf-k8s-was-ci-%RANDOM%"
if not defined PYTHON_CMD set "PYTHON_CMD=python"

call :require_command terraform || goto :error
call :require_command docker || goto :error
call :require_command curl || goto :error
call :require_command "%PYTHON_CMD%" || goto :error

docker info >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker Desktop is not running in Linux container mode.
  goto :error
)

echo [1/7] Check required CI files and terraform.tfvars
"%PYTHON_CMD%" "%ROOT_DIR%\scripts\ci\verify_ci_config.py" --root "%ROOT_DIR%"
if errorlevel 1 goto :error

echo [2/7] Terraform format check
terraform -chdir="%INFRA_DIR%" fmt -check -recursive
if errorlevel 1 goto :error

echo [3/7] Terraform init without backend
terraform -chdir="%INFRA_DIR%" init -backend=false -input=false
if errorlevel 1 goto :error

echo [4/7] Terraform validate
terraform -chdir="%INFRA_DIR%" validate
if errorlevel 1 goto :error

echo [5/7] WAS Python syntax check
"%PYTHON_CMD%" -m py_compile "%ROOT_DIR%\apps\was\app.py"
if errorlevel 1 goto :error

echo [6/7] Build and health-check WEB image
docker build --platform linux/amd64 --tag "%WEB_IMAGE%" "%ROOT_DIR%\apps\web"
if errorlevel 1 goto :error

docker run --detach --name "%WEB_CONTAINER%" --add-host was-service:127.0.0.1 --publish 18080:80 "%WEB_IMAGE%" >nul
if errorlevel 1 goto :error
call :wait_for_url "http://127.0.0.1:18080/health" "WEB"
if errorlevel 1 goto :error

echo [7/7] Build and health-check WAS image
docker build --platform linux/amd64 --tag "%WAS_IMAGE%" "%ROOT_DIR%\apps\was"
if errorlevel 1 goto :error

docker run --detach --name "%WAS_CONTAINER%" --publish 18000:8000 "%WAS_IMAGE%" >nul
if errorlevel 1 goto :error
call :wait_for_url "http://127.0.0.1:18000/health" "WAS"
if errorlevel 1 goto :error

call :cleanup
echo.
echo CI validation completed successfully.
exit /b 0

:wait_for_url
set "CHECK_URL=%~1"
set "CHECK_NAME=%~2"
for /L %%N in (1,1,30) do (
  curl --fail --silent --show-error "!CHECK_URL!" >nul 2>&1
  if not errorlevel 1 (
    echo [OK] !CHECK_NAME! health check
    exit /b 0
  )
  timeout /t 1 /nobreak >nul
)
echo [ERROR] !CHECK_NAME! health check failed: !CHECK_URL!
exit /b 1

:require_command
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required command not found: %~1
  exit /b 1
)
exit /b 0

:cleanup
docker rm -f "%WEB_CONTAINER%" "%WAS_CONTAINER%" >nul 2>&1
exit /b 0

:error
call :cleanup
echo.
echo [ERROR] Local CI validation failed.
exit /b 1

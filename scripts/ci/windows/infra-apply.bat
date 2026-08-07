@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

rem ============================================================
rem Apply AWS resources required by GitHub Actions CI.
rem DNS lookup is advisory only. Actual AWS CLI/Terraform calls
rem determine success or failure.
rem ============================================================

for %%I in ("%~dp0..\..\..") do set "ROOT_DIR=%%~fI"
set "INFRA_DIR=%ROOT_DIR%\infra"
set "PLAN_FILE=%TEMP%\tf-k8s-ci-%RANDOM%-%RANDOM%.tfplan"
set "AUTO_APPROVE=false"
set "MAX_RETRY=3"

if /I "%~1"=="-auto-approve" set "AUTO_APPROVE=true"
if /I "%~1"=="--auto-approve" set "AUTO_APPROVE=true"

call :require_command terraform || goto :error
call :require_command aws || goto :error
call :require_command powershell || goto :error

if not exist "%INFRA_DIR%\terraform.tfvars" (
  echo [ERROR] Missing file:
  echo         %INFRA_DIR%\terraform.tfvars
  goto :error
)

call :check_dns_warning iam.amazonaws.com
call :check_dns_warning sts.amazonaws.com

call :retry_aws_identity
if errorlevel 1 (
  echo [ERROR] AWS authentication or network access failed.
  echo         Run: aws sts get-caller-identity
  echo         Check AWS credentials, VPN, proxy, firewall, and DNS.
  goto :error
)

echo.
echo ============================================================
echo [1/5] Terraform fmt
echo ============================================================
terraform -chdir="%INFRA_DIR%" fmt -recursive
if errorlevel 1 goto :error

echo.
echo ============================================================
echo [2/5] Terraform init
echo ============================================================
terraform -chdir="%INFRA_DIR%" init
if errorlevel 1 goto :error

echo.
echo ============================================================
echo [3/5] Terraform validate
echo ============================================================
terraform -chdir="%INFRA_DIR%" validate
if errorlevel 1 goto :error

echo.
echo ============================================================
echo [4/5] Terraform plan
echo ============================================================
call :terraform_plan_retry
if errorlevel 1 goto :error

if /I "%AUTO_APPROVE%"=="false" (
  echo.
  choice /C YN /N /M "Apply this Terraform plan? [Y/N]: "
  if errorlevel 2 (
    echo Terraform apply cancelled.
    call :cleanup
    exit /b 0
  )
)

echo.
echo ============================================================
echo [5/5] Terraform apply
echo ============================================================
call :terraform_apply_retry
if errorlevel 1 goto :error

call :cleanup

echo.
echo ============================================================
echo GitHub Actions CI AWS resources completed
echo ============================================================
terraform -chdir="%INFRA_DIR%" output -raw github_actions_ci_role_arn
echo.
exit /b 0

:check_dns_warning
powershell -NoProfile -Command "try { Resolve-DnsName '%~1' -Type A -ErrorAction Stop | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo [WARN] DNS pre-check failed: %~1
  echo        Continuing because actual AWS CLI calls may still succeed.
  exit /b 0
)
echo [OK] DNS: %~1
exit /b 0

:retry_aws_identity
set /a ATTEMPT=1
:retry_aws_identity_loop
aws sts get-caller-identity >nul 2>&1
if not errorlevel 1 (
  echo [OK] AWS STS authentication
  exit /b 0
)
if %ATTEMPT% GEQ %MAX_RETRY% exit /b 1
echo [WARN] AWS STS check failed. Retrying %ATTEMPT%/%MAX_RETRY%...
timeout /t 5 /nobreak >nul
set /a ATTEMPT+=1
goto :retry_aws_identity_loop

:terraform_plan_retry
set /a ATTEMPT=1
:terraform_plan_retry_loop
terraform -chdir="%INFRA_DIR%" plan -out="%PLAN_FILE%"
if not errorlevel 1 exit /b 0
if %ATTEMPT% GEQ %MAX_RETRY% exit /b 1
echo.
echo [WARN] Terraform plan failed. Retrying %ATTEMPT%/%MAX_RETRY%...
call :check_dns_warning iam.amazonaws.com
timeout /t 5 /nobreak >nul
set /a ATTEMPT+=1
goto :terraform_plan_retry_loop

:terraform_apply_retry
set /a ATTEMPT=1
:terraform_apply_retry_loop
terraform -chdir="%INFRA_DIR%" apply "%PLAN_FILE%"
if not errorlevel 1 exit /b 0
if %ATTEMPT% GEQ %MAX_RETRY% exit /b 1
echo.
echo [WARN] Terraform apply failed. Retrying %ATTEMPT%/%MAX_RETRY%...
call :check_dns_warning iam.amazonaws.com
timeout /t 5 /nobreak >nul
set /a ATTEMPT+=1
goto :terraform_apply_retry_loop

:require_command
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required command not found: %~1
  exit /b 1
)
exit /b 0

:cleanup
if exist "%PLAN_FILE%" del /q "%PLAN_FILE%" >nul 2>&1
exit /b 0

:error
call :cleanup
echo.
echo [ERROR] CI infrastructure apply failed.
exit /b 1

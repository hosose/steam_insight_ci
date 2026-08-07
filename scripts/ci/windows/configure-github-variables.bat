@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "ROOT_DIR=%%~fI"
set "INFRA_DIR=%ROOT_DIR%\infra"
set "TARGET_REPOSITORY=%~1"
set "MAX_RETRY=3"

call :require_command terraform || goto :error
call :require_command gh || goto :error

gh auth status >nul 2>&1
if errorlevel 1 (
  echo [ERROR] GitHub CLI login required: gh auth login
  goto :error
)

if not defined TARGET_REPOSITORY (
  pushd "%ROOT_DIR%" >nul
  for /f "usebackq delims=" %%A in (`gh repo view --json nameWithOwner --jq ".nameWithOwner" 2^>nul`) do set "TARGET_REPOSITORY=%%A"
  popd >nul
)

if not defined TARGET_REPOSITORY (
  echo [ERROR] GitHub repository could not be detected.
  echo         Specify OWNER/REPO explicitly.
  goto :error
)

call :terraform_output AWS_REGION aws_region || goto :error
call :terraform_output AWS_ROLE_ARN github_actions_ci_role_arn || goto :error
call :terraform_output ECR_WEB_REPOSITORY web_ecr_repository_name || goto :error
call :terraform_output ECR_WAS_REPOSITORY was_ecr_repository_name || goto :error

if /I "%AWS_ROLE_ARN%"=="null" goto :missing_role
if "%AWS_ROLE_ARN%"=="" goto :missing_role

call :set_variable AWS_REGION "%AWS_REGION%" || goto :error
call :set_variable AWS_ROLE_ARN "%AWS_ROLE_ARN%" || goto :error
call :set_variable ECR_WEB_REPOSITORY "%ECR_WEB_REPOSITORY%" || goto :error
call :set_variable ECR_WAS_REPOSITORY "%ECR_WAS_REPOSITORY%" || goto :error

echo.
echo GitHub Repository variables configured: %TARGET_REPOSITORY%
gh variable list --repo "%TARGET_REPOSITORY%"
exit /b 0

:set_variable
set "VARIABLE_NAME=%~1"
set "VARIABLE_VALUE=%~2"
set /a ATTEMPT=1
:set_variable_loop
gh variable set "%VARIABLE_NAME%" --repo "%TARGET_REPOSITORY%" --body "%VARIABLE_VALUE%"
if not errorlevel 1 exit /b 0
if !ATTEMPT! GEQ %MAX_RETRY% exit /b 1
echo [WARN] GitHub variable update failed: %VARIABLE_NAME%. Retry !ATTEMPT!/%MAX_RETRY%...
timeout /t 3 /nobreak >nul
set /a ATTEMPT+=1
goto :set_variable_loop

:terraform_output
set "%~1="
for /f "usebackq delims=" %%A in (`terraform -chdir^="%INFRA_DIR%" output -raw %~2 2^>nul`) do set "%~1=%%A"
call set "OUTPUT_VALUE=%%%~1%%"
if not defined OUTPUT_VALUE (
  echo [ERROR] Terraform output not found: %~2
  exit /b 1
)
set "OUTPUT_VALUE="
exit /b 0

:require_command
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required command not found: %~1
  exit /b 1
)
exit /b 0

:missing_role
echo [ERROR] GitHub Actions CI Role was not created.
echo         Check enable_github_actions_ci=true and Terraform apply.
goto :error

:error
echo.
echo [ERROR] GitHub Repository variable configuration failed.
exit /b 1

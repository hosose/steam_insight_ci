@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "ROOT_DIR=%%~fI"
set "INFRA_DIR=%ROOT_DIR%\infra"
set "TARGET_REPOSITORY=%~1"
if not defined PYTHON_CMD set "PYTHON_CMD=python"

call :require_command terraform || goto :error
call :require_command gh || goto :error
call :require_command aws || goto :error
call :require_command "%PYTHON_CMD%" || goto :error

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
  goto :error
)

"%PYTHON_CMD%" "%ROOT_DIR%\scripts\ci\verify_ci_config.py" --root "%ROOT_DIR%" --repo "%TARGET_REPOSITORY%"
if errorlevel 1 goto :error

call :terraform_output AWS_REGION aws_region || goto :error
call :terraform_output AWS_ROLE_ARN github_actions_ci_role_arn || goto :error
call :terraform_output ECR_WEB_REPOSITORY web_ecr_repository_name || goto :error
call :terraform_output ECR_WAS_REPOSITORY was_ecr_repository_name || goto :error

if /I "%AWS_ROLE_ARN%"=="null" goto :missing_role
if "%AWS_ROLE_ARN%"=="" goto :missing_role

call :check_variable AWS_REGION "%AWS_REGION%" || goto :error
call :check_variable AWS_ROLE_ARN "%AWS_ROLE_ARN%" || goto :error
call :check_variable ECR_WEB_REPOSITORY "%ECR_WEB_REPOSITORY%" || goto :error
call :check_variable ECR_WAS_REPOSITORY "%ECR_WAS_REPOSITORY%" || goto :error

for %%A in ("%AWS_ROLE_ARN%") do set "ROLE_NAME=%%~nxA"
aws iam get-role --role-name "%ROLE_NAME%" >nul
if errorlevel 1 (
  echo [ERROR] AWS IAM Role not found: %ROLE_NAME%
  goto :error
)
echo [OK] AWS IAM Role: %ROLE_NAME%

aws ecr describe-repositories --region "%AWS_REGION%" --repository-names "%ECR_WEB_REPOSITORY%" "%ECR_WAS_REPOSITORY%" >nul
if errorlevel 1 (
  echo [ERROR] ECR repositories could not be verified.
  goto :error
)
echo [OK] ECR repositories: %ECR_WEB_REPOSITORY%, %ECR_WAS_REPOSITORY%

gh workflow view ci.yml --repo "%TARGET_REPOSITORY%" >nul 2>&1
if errorlevel 1 (
  echo [WARN] ci.yml is not visible on GitHub yet. Push the repository first.
) else (
  echo [OK] GitHub Workflow: ci.yml
)

echo.
echo CI remote configuration verified: %TARGET_REPOSITORY%
exit /b 0

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

:check_variable
set "VARIABLE_NAME=%~1"
set "EXPECTED_VALUE=%~2"
set "ACTUAL_VALUE="
for /f "usebackq delims=" %%A in (`gh variable get "%VARIABLE_NAME%" --repo "%TARGET_REPOSITORY%" 2^>nul`) do set "ACTUAL_VALUE=%%A"
if not defined ACTUAL_VALUE (
  echo [ERROR] GitHub variable not found: %VARIABLE_NAME%
  exit /b 1
)
if not "!ACTUAL_VALUE!"=="!EXPECTED_VALUE!" (
  echo [ERROR] GitHub variable differs from Terraform output: %VARIABLE_NAME%
  echo         GitHub  : !ACTUAL_VALUE!
  echo         Terraform: !EXPECTED_VALUE!
  exit /b 1
)
echo [OK] GitHub variable %VARIABLE_NAME%
exit /b 0

:require_command
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required command not found: %~1
  exit /b 1
)
exit /b 0

:missing_role
echo [ERROR] GitHub Actions CI Role Terraform output is missing.
echo         Check enable_github_actions_ci=true and Terraform apply.
goto :error

:error
echo.
echo [ERROR] CI remote configuration verification failed.
exit /b 1

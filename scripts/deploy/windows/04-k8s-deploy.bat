@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

rem ============================================================
rem Windows Kubernetes manual deployment helper.
rem Uses tf-k8s-cd\k8s\overlays\dev Kustomize structure.
rem If CHANGE_ME placeholders remain, a temporary copy is patched
rem with Terraform ECR outputs. The CD repository is not modified.
rem ============================================================

for %%I in ("%~dp0..\..\..") do set "SOURCE_REPO_ROOT=%%~fI"
set "INFRA_DIR=%SOURCE_REPO_ROOT%\infra"

if not defined GITOPS_REPO_DIR (
  for %%I in ("%SOURCE_REPO_ROOT%\..\devops_tf_k8s_cd") do set "GITOPS_REPO_DIR=%%~fI"
)

set "K8S_SOURCE_DIR=%GITOPS_REPO_DIR%\k8s"
set "SOURCE_KUSTOMIZATION=%K8S_SOURCE_DIR%\overlays\dev\kustomization.yaml"

if not defined APP_NAMESPACE set "APP_NAMESPACE=de-ai-07"
if not defined IMAGE_TAG set "IMAGE_TAG=k8s-auto"

call :require_command terraform
if errorlevel 1 goto :error
call :require_command aws
if errorlevel 1 goto :error
call :require_command kubectl
if errorlevel 1 goto :error
call :require_command powershell.exe
if errorlevel 1 goto :error

if not exist "%INFRA_DIR%" goto :infra_not_found
if not exist "%SOURCE_KUSTOMIZATION%" goto :manifest_not_found

call :terraform_output AWS_REGION aws_region
if errorlevel 1 goto :error
call :terraform_output CLUSTER_NAME cluster_name
if errorlevel 1 goto :error
call :terraform_output WEB_REPO web_ecr_repository_url
if errorlevel 1 goto :error
call :terraform_output WAS_REPO was_ecr_repository_url
if errorlevel 1 goto :error

aws eks update-kubeconfig --region "%AWS_REGION%" --name "%CLUSTER_NAME%" >nul
if errorlevel 1 goto :error

set "TEMP_ROOT=%TEMP%\tf-k8s-kustomize-%RANDOM%-%RANDOM%"
set "TEMP_K8S_DIR=%TEMP_ROOT%\k8s"
set "TEMP_KUSTOMIZE_DIR=%TEMP_K8S_DIR%\overlays\dev"
set "TEMP_KUSTOMIZATION=%TEMP_KUSTOMIZE_DIR%\kustomization.yaml"

mkdir "%TEMP_ROOT%" >nul 2>&1
if errorlevel 1 goto :temp_error

xcopy "%K8S_SOURCE_DIR%" "%TEMP_K8S_DIR%\" /E /I /Q /Y >nul
if errorlevel 1 goto :temp_error

if not exist "%TEMP_KUSTOMIZATION%" goto :temp_error

set "PATCH_FILE=%TEMP_KUSTOMIZATION%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p=$env:PATCH_FILE;" ^
  "$c=[System.IO.File]::ReadAllText($p);" ^
  "$c=$c.Replace('CHANGE_ME_WEB_ECR',$env:WEB_REPO);" ^
  "$c=$c.Replace('CHANGE_ME_WAS_ECR',$env:WAS_REPO);" ^
  "$c=[regex]::Replace($c,'(?ms)(- name:\s*WEB_IMAGE\s*\r?\n\s*newName:\s*[^\r\n]+\s*\r?\n\s*newTag:)\s*[^\r\n]+','$1 '+$env:IMAGE_TAG);" ^
  "$c=[regex]::Replace($c,'(?ms)(- name:\s*WAS_IMAGE\s*\r?\n\s*newName:\s*[^\r\n]+\s*\r?\n\s*newTag:)\s*[^\r\n]+','$1 '+$env:IMAGE_TAG);" ^
  "[System.IO.File]::WriteAllText($p,$c,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 goto :patch_error

findstr /C:"CHANGE_ME" "%TEMP_KUSTOMIZATION%" >nul 2>&1
if not errorlevel 1 goto :placeholder_error

echo.
echo [INFO] Applying Kustomize overlay.
echo        Source : %SOURCE_KUSTOMIZATION%
echo        WEB    : %WEB_REPO%:%IMAGE_TAG%
echo        WAS    : %WAS_REPO%:%IMAGE_TAG%
echo.

kubectl apply -k "%TEMP_KUSTOMIZE_DIR%"
if errorlevel 1 goto :error

kubectl rollout status deployment/was -n "%APP_NAMESPACE%" --timeout=20m
if errorlevel 1 goto :error

kubectl rollout status deployment/web -n "%APP_NAMESPACE%" --timeout=20m
if errorlevel 1 goto :error

echo.
kubectl get pods,svc,ingress,hpa,pdb -n "%APP_NAMESPACE%"
if errorlevel 1 goto :error

echo.
echo [OK] Kubernetes deployment completed.
echo      GitOps Repository: %GITOPS_REPO_DIR%
echo      WEB_IMAGE=%WEB_REPO%:%IMAGE_TAG%
echo      WAS_IMAGE=%WAS_REPO%:%IMAGE_TAG%
call :cleanup
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

:require_command
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required command not found: %~1
  exit /b 1
)
exit /b 0

:cleanup
if defined TEMP_ROOT if exist "%TEMP_ROOT%" rmdir /S /Q "%TEMP_ROOT%" >nul 2>&1
exit /b 0

:infra_not_found
echo.
echo [ERROR] Terraform infra directory not found.
echo         %INFRA_DIR%
exit /b 1

:manifest_not_found
echo.
echo [ERROR] Kustomize file not found.
echo         %SOURCE_KUSTOMIZATION%
echo.
echo Expected repositories:
echo   parent\tf-k8s-ci
echo   parent\tf-k8s-cd
call :cleanup
exit /b 1

:temp_error
echo.
echo [ERROR] Failed to create temporary Kustomize directory.
call :cleanup
exit /b 1

:patch_error
echo.
echo [ERROR] Failed to patch temporary Kustomize file.
call :cleanup
exit /b 1

:placeholder_error
echo.
echo [ERROR] CHANGE_ME remains after temporary patch.
echo         %TEMP_KUSTOMIZATION%
call :cleanup
exit /b 1

:error
echo.
echo [ERROR] Kubernetes deployment failed.
call :cleanup
exit /b 1

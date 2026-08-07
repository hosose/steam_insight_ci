@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "COMMAND=%~1"

if not defined COMMAND goto :help
if /I "%COMMAND%"=="deploy" goto :deploy
if /I "%COMMAND%"=="infra" goto :infra
if /I "%COMMAND%"=="image" goto :image
if /I "%COMMAND%"=="secret" goto :secret
if /I "%COMMAND%"=="k8s" goto :k8s
if /I "%COMMAND%"=="status" goto :status
if /I "%COMMAND%"=="destroy" goto :destroy
if /I "%COMMAND%"=="ci-setup" goto :ci_setup
if /I "%COMMAND%"=="ci-validate" goto :ci_validate
if /I "%COMMAND%"=="ci-check" goto :ci_check
if /I "%COMMAND%"=="env-setup" goto :env_setup
if /I "%COMMAND%"=="help" goto :help
if /I "%COMMAND%"=="-h" goto :help
if /I "%COMMAND%"=="--help" goto :help

echo [ERROR] 알 수 없는 명령: %COMMAND%
echo.
goto :help_error

:deploy
call "%PROJECT_ROOT%\scripts\deploy\windows\deploy-all.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:infra
call "%PROJECT_ROOT%\scripts\deploy\windows\01-infra-apply.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:image
call "%PROJECT_ROOT%\scripts\deploy\windows\02-image-build-push.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:secret
call "%PROJECT_ROOT%\scripts\deploy\windows\03-secret-sync.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:k8s
call "%PROJECT_ROOT%\scripts\deploy\windows\04-k8s-deploy.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:status
call "%PROJECT_ROOT%\scripts\ops\windows\status.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:destroy
call "%PROJECT_ROOT%\scripts\ops\windows\destroy.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:ci_setup
call "%PROJECT_ROOT%\scripts\ci\windows\setup-all.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:ci_validate
call "%PROJECT_ROOT%\scripts\ci\windows\validate.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:ci_check
call "%PROJECT_ROOT%\scripts\ci\windows\verify-github.bat" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:env_setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\setup\windows\setup-windows-eks.ps1" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:help
echo 사용법: project.bat ^<명령^> [옵션]
echo.
echo 최초 구축/배포
echo   deploy                  인프라 생성 ^> 이미지 Push ^> Secret ^> Kubernetes 배포
echo   infra                   Terraform 인프라만 생성하고 kubeconfig 연결
echo   image                   WEB/WAS 이미지를 빌드하여 ECR Push
echo   secret                  RDS 정보를 Kubernetes Secret으로 동기화
echo   k8s                     tf-k8s-cd Manifest를 수동 적용
echo.
echo 운영
echo   status                  EKS 및 애플리케이션 상태 확인
echo   destroy                 Kubernetes 리소스와 Terraform 인프라 삭제
echo.
echo CI
echo   ci-setup [OWNER/REPO]   OIDC/IAM 반영 ^> GitHub 변수 등록 ^> 로컬 검증
echo   ci-validate             Terraform/Docker 로컬 CI 검증만 수행
echo   ci-check [OWNER/REPO]   GitHub 변수, IAM Role, ECR, Workflow 연결 확인
echo.
echo Windows 환경
echo   env-setup               WSL2, Docker Desktop 및 필수 도구 설치
echo.
echo 예시
echo   set "IMAGE_TAG=k8s-auto"
echo   project.bat deploy
echo   project.bat ci-setup GitHub_ID/tf-k8s-ci
echo   project.bat ci-check GitHub_ID/tf-k8s-ci
echo   project.bat status
echo   project.bat destroy
exit /b 0

:help_error
call :help
exit /b 1

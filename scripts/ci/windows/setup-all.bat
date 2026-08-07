@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

rem ============================================================
rem Windows CI one-time setup
rem Usage:
rem   setup-ci.bat [OWNER/REPO] [--auto-approve]
rem   project.bat ci-setup [OWNER/REPO] [--auto-approve]
rem ============================================================

set "TARGET_REPOSITORY="
set "AUTO_APPROVE=false"
if not defined PYTHON_CMD set "PYTHON_CMD=python"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..\..") do set "ROOT_DIR=%%~fI"

:parse_args
if "%~1"=="" goto :run
if /I "%~1"=="--auto-approve" (
  set "AUTO_APPROVE=true"
  shift
  goto :parse_args
)
if /I "%~1"=="-auto-approve" (
  set "AUTO_APPROVE=true"
  shift
  goto :parse_args
)
if /I "%~1"=="-h" goto :help
if /I "%~1"=="--help" goto :help
set "CURRENT_ARGUMENT=%~1"
if "!CURRENT_ARGUMENT:~0,1!"=="-" (
  echo [ERROR] Unsupported option: %~1
  goto :help_error
)
if defined TARGET_REPOSITORY (
  echo [ERROR] Only one GitHub repository may be specified.
  goto :help_error
)
set "TARGET_REPOSITORY=%~1"
shift
goto :parse_args

:run
call :require_command "%PYTHON_CMD%" || goto :error

call :section "[1/5] CI configuration pre-check"
if defined TARGET_REPOSITORY (
  "%PYTHON_CMD%" "%ROOT_DIR%\scripts\ci\verify_ci_config.py" --root "%ROOT_DIR%" --repo "%TARGET_REPOSITORY%"
) else (
  "%PYTHON_CMD%" "%ROOT_DIR%\scripts\ci\verify_ci_config.py" --root "%ROOT_DIR%"
)
if errorlevel 1 goto :error

call :section "[2/5] Apply AWS infrastructure for GitHub Actions CI"
if /I "%AUTO_APPROVE%"=="true" (
  call "%SCRIPT_DIR%infra-apply.bat" --auto-approve
) else (
  call "%SCRIPT_DIR%infra-apply.bat"
)
if errorlevel 1 goto :error

call :section "[3/5] Configure GitHub Repository Variables"
if defined TARGET_REPOSITORY (
  call "%SCRIPT_DIR%configure-github-variables.bat" "%TARGET_REPOSITORY%"
) else (
  call "%SCRIPT_DIR%configure-github-variables.bat"
)
if errorlevel 1 goto :error

call :section "[4/5] Validate local CI configuration"
call "%SCRIPT_DIR%validate.bat"
if errorlevel 1 goto :error

call :section "[5/5] Verify AWS and GitHub CI connection"
if defined TARGET_REPOSITORY (
  call "%SCRIPT_DIR%verify-github.bat" "%TARGET_REPOSITORY%"
) else (
  call "%SCRIPT_DIR%verify-github.bat"
)
if errorlevel 1 goto :error

echo.
echo ============================================================
echo CI setup completed
echo ============================================================
echo.
echo Next commands:
echo   git add .
echo   git commit -m "feat: add GitHub Actions CI with AWS OIDC"
echo   git push origin main
exit /b 0

:section
echo.
echo ============================================================
echo %~1
echo ============================================================
exit /b 0

:require_command
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required command not found: %~1
  exit /b 1
)
exit /b 0

:help
echo Usage:
echo   setup-ci.bat [OWNER/REPO] [--auto-approve]
echo   project.bat ci-setup [OWNER/REPO] [--auto-approve]
echo.
echo Example:
echo   setup-ci.bat GitHub_ID/tf-k8s-ci
echo   setup-ci.bat GitHub_ID/tf-k8s-ci --auto-approve
exit /b 0

:help_error
call :help
exit /b 1

:error
echo.
echo [ERROR] Windows CI setup stopped.
exit /b 1

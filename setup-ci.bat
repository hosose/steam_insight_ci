@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

call "%~dp0scripts\ci\windows\setup-all.bat" %*
exit /b %ERRORLEVEL%

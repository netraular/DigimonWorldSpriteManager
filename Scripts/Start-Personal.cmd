@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0Start-Personal.ps1"
exit /b %ERRORLEVEL%

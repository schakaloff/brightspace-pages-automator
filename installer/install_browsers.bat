@echo off
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0_internal\playwright\driver\package\.local-browsers"
"%~dp0_internal\playwright\driver\node.exe" "%~dp0_internal\playwright\driver\package\cli.js" install chromium

@echo off
rem Double-click to open Paper Reader (no console). Falls back to python if pythonw is missing.
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel%==0 (start "" pythonw "%~dp0rp_app.pyw") else (start "" python "%~dp0rp_app.pyw")

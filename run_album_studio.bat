@echo off
REM Double-click launcher for Album Studio. Starts the single consolidated web app
REM (src/app.py) and opens the browser automatically. No AI model is loaded at startup --
REM it only loads on demand (running the Qwen pipeline stage, or first use of the spread
REM editor's chat).
cd /d "%~dp0"
".venv\Scripts\python.exe" "src\app.py" --db "cache\project_full.db" --exports "exports" --spreads "exports\spreads.json" --crops "exports\crops.json" --rendered-dir "exports\rendered_spreads" --out-pdf "exports\album.pdf"
pause

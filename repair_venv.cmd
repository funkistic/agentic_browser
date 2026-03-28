@echo off
echo === Rebuilding Python virtual environment ===

rem Delete broken venv
rmdir /s /q "K:\agent_browser\src-tauri\agent-env"

echo Creating fresh venv...
python -m venv "K:\agent_browser\src-tauri\agent-env"
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.12 from https://python.org and re-run this script.
    pause
    exit /b 1
)

echo Installing required packages...
"K:\agent_browser\src-tauri\agent-env\Scripts\pip.exe" install fastapi uvicorn python-dotenv browser-use playwright google-generativeai google-genai groq litellm open-interpreter langchain-google-genai aiohttp pydantic-settings sse-starlette

echo Installing Playwright browsers...
"K:\agent_browser\src-tauri\agent-env\Scripts\playwright.exe" install chromium

echo.
echo === Done! Agent backend is ready. Run .\run_dev.cmd to start the app. ===
pause

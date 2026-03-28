@echo off
echo Terminating any running python servers...
taskkill /f /im uvicorn.exe /t >nul 2>&1
taskkill /f /im python.exe /t >nul 2>&1

echo Ignoring locked agent-env and creating a brand new agent-env2...

cd K:\agent_browser\src-tauri
if exist "agent-env2" (
    rmdir /s /q "agent-env2"
)

echo Creating fresh venv in agent-env2...
python -m venv "agent-env2"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create virtual environment. Ensure python is installed.
    pause
    exit /b 1
)

echo Activating new venv and installing packages...
call "agent-env2\Scripts\activate.bat"

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing dependencies...
# Installing core dependencies 
pip install "fastapi" "uvicorn[standard]" "browser-use" "playwright" "pydantic" "python-dotenv" "aiohttp" "requests" "undetected-chromedriver"

echo Checking and installing Playwright tools inside the new env...
playwright install chromium

echo.
echo ✅ agent-env2 has been successfully rebuilt from scratch!
echo ✅ You can now run your project using .\run_dev.cmd
exit /b 0

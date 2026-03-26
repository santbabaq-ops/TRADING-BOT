@echo off
title Polymarket RBI Bot
color 0A

:: Move to project directory FIRST (handles spaces in path)
cd /d "%~dp0"

echo.
echo  ============================================
echo    Polymarket RBI Bot - Launcher
echo  ============================================
echo.

:: Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERREUR] Python non trouve. Installe Python 3.10+
    echo  https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Create venv if needed
if not exist ".venv" (
    echo  [1/2] Creation de l'environnement virtuel...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERREUR] Impossible de creer le venv.
        pause
        exit /b 1
    )
)

:: Activate venv
call ".venv\Scripts\activate.bat"
echo  [OK] Environnement virtuel active.

:: Always ensure dependencies are installed
echo  [OK] Verification des dependances...
pip install -r requirements.txt --quiet 2>nul
echo  [OK] Dependances OK.

:: Check .env
if not exist ".env" (
    echo.
    echo  [ATTENTION] Fichier .env manquant.
    echo  Copie de .env.example vers .env...
    copy ".env.example" ".env" >nul
    echo  Edite .env avec tes cles avant de trader en live.
    echo.
)

echo.
echo  Demarrage du serveur sur http://localhost:1818
echo  Dashboard: http://localhost:1818
echo  API docs:  http://localhost:1818/docs
echo  Ctrl+C pour arreter
echo.

:: Open browser after 2 seconds
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:1818"

:: Launch server
python api/server.py

pause

@echo off
echo ========================================================
echo   OSRS GE QUANT - BLOOMBERG TERMINAL LAUNCHER
echo ========================================================
cd /d "c:\Users\londo\OneDrive\Desktop\osrs-ge-quant"

echo [1/4] Setting up Environment...
set PYTHONPATH=src
set PYTHON_EXE=C:\Users\londo\anaconda3\envs\osrs_ge_quant\python.exe

echo [2/3] Starting OSRS Bloomberg Dashboard Server & Sentinel Sentry Daemon...
start "OSRS Bloomberg Terminal" "%PYTHON_EXE%" -m osrs_ge_quant.cli dashboard

echo [3/3] Opening OSRS Bloomberg Terminal in your browser...
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8050/

echo ========================================================
echo   Launcher complete. Dashboard is running in the other window.
echo ========================================================
timeout /t 5
exit

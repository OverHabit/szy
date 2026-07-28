@echo off
cd /d "%~dp0"
title A-Share Strategy Lab
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
echo.
echo The app has stopped. Press any key to close this window.
pause >nul

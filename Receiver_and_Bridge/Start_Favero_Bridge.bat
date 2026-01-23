@echo off
REM ===============================
REM Favero OBS Bridge Startup Script
REM ===============================

REM 1. Ensure required Python modules are installed
python -m pip install --upgrade pip
python -m pip install pyserial obs-websocket-py

REM 2. Navigate to the folder containing the bridge
cd "C:\Users\Ben SSD\Downloads"

REM 3. Run the bridge script
python Favero_OBS_Bridge.py

pause

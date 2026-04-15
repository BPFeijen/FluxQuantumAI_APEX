@echo off
REM ================================================================
REM  launch_demo_asia.bat
REM  FluxQuantumAI — Demo mode for Asia session
REM  Mode: DRY RUN (signals logged, NO real orders placed)
REM  Strategy: V2 rule-based (ALPHA + BETA + GAMMA + pullback)
REM  Start: after 22:00 UTC (GC maintenance ends)
REM  Fixes active: FIX1-4 + BUG1-2 + SL_guard + GAMMA
REM ================================================================

echo.
echo ============================================================
echo   FluxQuantumAI DEMO — Asia Session
echo   Mode: DRY RUN (no real orders)
echo   Time: %DATE% %TIME%
echo ============================================================
echo.

cd /d C:\FluxQuantumAI

REM Check capture services before starting
echo [1] Checking capture services...
python -c "
import socket, sys
ok = True
for port, name in [(8000,'quantower_level2_api'),(8002,'iceberg_receiver')]:
    s = socket.socket(); s.settimeout(2)
    try: s.connect(('127.0.0.1', port)); s.close(); print(f'  OK: {name} (port {port})')
    except: print(f'  WARN: {name} (port {port}) not responding'); ok = False
sys.exit(0 if ok else 1)
"
if errorlevel 1 (
    echo.
    echo WARNING: One or more capture services not responding.
    echo The system will start but feed may be stale.
    echo.
) else (
    echo   Capture services: OK
)

echo.
echo [2] Starting DEMO pipeline...
echo     Logs: C:\FluxQuantumAI\logs\demo_asia_%DATE:~-4,4%%DATE:~-10,2%%DATE:~-7,2%.log
echo.

python -X utf8 run_live.py --dry_run > logs\demo_asia_%DATE:~-4,4%%DATE:~-10,2%%DATE:~-7,2%.log 2>&1

echo.
echo Demo session ended.
pause

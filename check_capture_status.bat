@echo off
REM =========================================================
REM  check_capture_status.bat — FluxQuantumAI Quick Status Check
REM  Run anytime to see the state of capture services.
REM =========================================================

echo.
echo ========================================================
echo   FluxQuantumAI Capture Status — %DATE% %TIME%
echo ========================================================
echo.

REM --- Quantower ---
echo [1] QUANTOWER (Starter.exe):
tasklist /FI "IMAGENAME eq Starter.exe" 2>nul | findstr /i "starter" >nul
if errorlevel 1 (
    echo     STATUS: NOT RUNNING ^<^<^< WARNING
) else (
    tasklist /FI "IMAGENAME eq Starter.exe" /FO TABLE /NH 2>nul
    echo     STATUS: RUNNING OK
)
echo.

REM --- L2 Capture (port 8000) ---
echo [2] L2 CAPTURE API (quantower_level2_api / port 8000):
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo     PORT 8000: NOT LISTENING ^<^<^< WARNING
) else (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
        echo     PORT 8000: LISTENING  PID=%%p
        tasklist /FI "PID eq %%p" /FO TABLE /NH 2>nul
    )
    echo     STATUS: RUNNING OK
)
echo.

REM --- Iceberg Receiver (port 8002) ---
echo [3] ICEBERG RECEIVER (iceberg_receiver.py / port 8002):
netstat -ano | findstr ":8002" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo     PORT 8002: NOT LISTENING ^<^<^< WARNING
) else (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8002" ^| findstr "LISTENING"') do (
        echo     PORT 8002: LISTENING  PID=%%p
        tasklist /FI "PID eq %%p" /FO TABLE /NH 2>nul
    )
    echo     STATUS: RUNNING OK
)
echo.

REM --- Most recent L2 data file ---
echo [4] MOST RECENT L2 DATA FILE:
for /f "delims=" %%f in ('powershell -NoProfile -Command "Get-ChildItem C:\data\level2\_gc_xcec\ | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object { $age = [int]((Get-Date) - $_.LastWriteTime).TotalSeconds; Write-Output (\"  File: \" + $_.Name + \"`n  Modified: \" + $_.LastWriteTime.ToString(\"yyyy-MM-dd HH:mm:ss\") + \"  (\" + $age + \"s ago)\") }"') do echo %%f
echo.

REM --- Watchdog task status ---
echo [5] WATCHDOG TASK (FluxQuantumAI_L2_Watchdog):
schtasks /query /tn "FluxQuantumAI_L2_Watchdog" /fo LIST /v 2>nul | findstr /i "Status Last.Run Next.Run"
if errorlevel 1 (
    echo     Watchdog task NOT installed. Run install_watchdog.bat as Administrator.
)
echo.

REM --- Last 10 lines of watchdog log ---
echo [6] LAST 10 LINES OF WATCHDOG LOG:
if exist "C:\FluxQuantumAI\logs\watchdog.log" (
    powershell -NoProfile -Command "Get-Content C:\FluxQuantumAI\logs\watchdog.log -Tail 10"
) else (
    echo     Log file not found: C:\FluxQuantumAI\logs\watchdog.log
)
echo.
echo ========================================================
pause

@echo off
REM ================================================================
REM  FluxQuantumAI APEX Full Startup
REM  Launches MT5 terminals first, waits, then starts trading services
REM  Run this after RDP login or server restart
REM ================================================================

echo [%TIME%] Starting FluxQuantumAI APEX...

REM 1. Launch Roboforex MT5 Demo (portable)
echo [%TIME%] Launching Roboforex MT5 Demo...
start "" "C:\MT5_RoboForex\terminal64.exe" /portable
timeout /t 15 /nointerrupt >nul

REM 2. Restart FluxQuantumAPEX service (will now find the terminal)
echo [%TIME%] Restarting FluxQuantumAPEX service...
C:\tools\nssm\nssm.exe restart FluxQuantumAPEX >nul 2>&1
if errorlevel 1 (
    C:\tools\nssm\nssm.exe start FluxQuantumAPEX >nul 2>&1
)
timeout /t 5 /nointerrupt >nul

REM 3. Restart FluxQuantumAPEX_Live service
echo [%TIME%] Restarting FluxQuantumAPEX_Live service...
C:\tools\nssm\nssm.exe restart FluxQuantumAPEX_Live >nul 2>&1
if errorlevel 1 (
    C:\tools\nssm\nssm.exe start FluxQuantumAPEX_Live >nul 2>&1
)

echo [%TIME%] All services started.
echo Check C:\FluxQuantumAI\logs\service_state.json for mt5_robo_connected
pause

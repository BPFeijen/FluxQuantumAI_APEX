@echo off
REM Launch Roboforex MT5 terminal, wait for login, then start APEX service
echo Starting Roboforex MT5...
start "" "C:\MT5_RoboForex\terminal64.exe"
echo Waiting 30s for MT5 login...
timeout /t 30 /nointeractive
echo Restarting FluxQuantumAPEX service...
C:\tools\nssm\nssm.exe restart FluxQuantumAPEX
echo Done. Check service_state.json for mt5_robo_connected.
pause

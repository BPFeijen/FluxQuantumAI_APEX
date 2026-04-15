@echo off
REM =========================================================
REM  install_watchdog.bat — FluxQuantumAI L2 Capture Watchdog
REM  Registers watchdog in Windows Task Scheduler as SYSTEM
REM  Run as Administrator.
REM =========================================================

SET TASK_NAME=FluxQuantumAI_L2_Watchdog
SET PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
SET SCRIPT=C:\FluxQuantumAI\watchdog_l2_capture.py
SET XML_PATH=%TEMP%\watchdog_task.xml

echo [%TIME%] Creating Task Scheduler XML...

REM Write XML via PowerShell to avoid encoding issues
powershell -NoProfile -Command "$xml = @'
<?xml version=""1.0"" encoding=""UTF-16""?>
<Task version=""1.4"" xmlns=""http://schemas.microsoft.com/windows/2004/02/mit/task"">
  <RegistrationInfo>
    <Description>FluxQuantumAI L2 Capture Watchdog — restarts Quantower, L2 API, and Iceberg receiver if they crash.</Description>
    <Author>FluxQuantumAI</Author>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </BootTrigger>
    <TimeTrigger>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2000-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id=""Author"">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>false</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context=""Author"">
    <Exec>
      <Command>C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe</Command>
      <Arguments>C:\FluxQuantumAI\watchdog_l2_capture.py</Arguments>
      <WorkingDirectory>C:\FluxQuantumAI</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@
[System.IO.File]::WriteAllText('%XML_PATH%', $xml, [System.Text.Encoding]::Unicode)"

if errorlevel 1 (
    echo [ERROR] Failed to create XML. Aborting.
    pause
    exit /b 1
)

echo [%TIME%] Removing existing task if present...
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

echo [%TIME%] Registering scheduled task...
schtasks /create /tn "%TASK_NAME%" /xml "%XML_PATH%" /f

if errorlevel 1 (
    echo [ERROR] schtasks /create failed.
    del "%XML_PATH%" >nul 2>&1
    pause
    exit /b 1
)

del "%XML_PATH%" >nul 2>&1

echo.
echo [%TIME%] Task registered. Starting watchdog now...
schtasks /run /tn "%TASK_NAME%"

echo.
echo [%TIME%] Verifying task status:
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i "Status Last Run Next Run"

echo.
echo [%TIME%] Watchdog installed and running.
echo   Task name : %TASK_NAME%
echo   Log file  : C:\FluxQuantumAI\logs\watchdog.log
echo.
pause

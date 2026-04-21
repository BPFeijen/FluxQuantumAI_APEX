@echo off
cd /d C:\FluxQuantumAI
"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" -u -W ignore run_live.py --execute --broker roboforex --lot_size 0.05 >> C:\FluxQuantumAI\logs\apex_interactive_stdout.log 2>> C:\FluxQuantumAI\logs\apex_interactive_stderr.log

import sys, os, traceback
sys.stdout = open("C:/FluxQuantumAI/logs/wrapper_stdout.log", "w")
sys.stderr = open("C:/FluxQuantumAI/logs/wrapper_stderr.log", "w")
os.chdir("C:/FluxQuantumAI")
try:
    print("wrapper started", flush=True)
    sys.argv = ["run_live.py", "--execute", "--broker", "roboforex", "--lot_size", "0.05"]
    exec(open("run_live.py").read())
except Exception as e:
    traceback.print_exc()
    sys.stderr.flush()

import MetaTrader5 as mt5

# Test 1: without path (connect to any terminal)
with open("C:/FluxQuantumAI/logs/mt5_test2.txt", "w") as f:
    f.write("Test 1: no path\n")
    ok = mt5.initialize(timeout=10000)
    if ok:
        info = mt5.account_info()
        f.write(f"CONNECTED login={info.login} balance={info.balance} server={info.server}\n")
        mt5.shutdown()
    else:
        f.write(f"FAILED {mt5.last_error()}\n")
    
    # Test 2: with path only
    f.write("\nTest 2: path only\n")
    ok = mt5.initialize(path=r"C:\MT5_RoboForex\terminal64.exe", timeout=10000)
    if ok:
        info = mt5.account_info()
        f.write(f"CONNECTED login={info.login} balance={info.balance}\n")
        mt5.shutdown()
    else:
        f.write(f"FAILED {mt5.last_error()}\n")
    
    # Test 3: with portable flag
    f.write("\nTest 3: path + portable\n")
    ok = mt5.initialize(path=r"C:\MT5_RoboForex\terminal64.exe", portable=True, timeout=10000)
    if ok:
        info = mt5.account_info()
        f.write(f"CONNECTED login={info.login} balance={info.balance}\n")
        mt5.shutdown()
    else:
        f.write(f"FAILED {mt5.last_error()}\n")

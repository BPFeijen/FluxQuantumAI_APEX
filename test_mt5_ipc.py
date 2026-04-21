import MetaTrader5 as mt5
ok = mt5.initialize(path=r"C:\MT5_RoboForex\terminal64.exe", login=68302120, password="BSRonja$040227!", server="RoboForex-Pro-3", timeout=10000)
if ok:
    info = mt5.account_info()
    with open("C:/FluxQuantumAI/logs/mt5_test_result.txt", "w") as f:
        f.write(f"CONNECTED login={info.login} balance={info.balance}\n")
    mt5.shutdown()
else:
    with open("C:/FluxQuantumAI/logs/mt5_test_result.txt", "w") as f:
        f.write(f"FAILED {mt5.last_error()}\n")

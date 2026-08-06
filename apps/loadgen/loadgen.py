"""Генератор фонового трафика. Без него все графики будут плоскими."""
import random, threading, time, urllib.request, os

BASE = os.getenv("TARGET", "http://payment-api.banking.svc.cluster.local:8000")
RPS = int(os.getenv("RPS", "20"))

def hit():
    try:
        if random.random() < 0.6:
            urllib.request.urlopen(f"{BASE}/balance/{random.randint(1000,9999)}", timeout=5)
        else:
            req = urllib.request.Request(f"{BASE}/payment", method="POST", data=b"")
            urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # ошибки нас не волнуют — их посчитает сам сервис

while True:
    for _ in range(RPS):
        threading.Thread(target=hit, daemon=True).start()
    time.sleep(1)

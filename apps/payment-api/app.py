"""
payment-api — учебный "банковский" сервис для observability-стенда.

Отдаёт:
  - технические метрики (RED: Rate, Errors, Duration)
  - бизнес-метрики (объём и количество платежей)
  - структурированные JSON-логи в stdout
  - управляемый хаос: задержки, ошибки, полный отказ
"""

import json
import logging
import os
import random
import sys
import time
import uuid

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

SERVICE = os.getenv("SERVICE_NAME", "payment-api")
POD = os.getenv("HOSTNAME", "local")

# ---------------------------------------------------------------- логирование
# Логи пишем в stdout одной строкой JSON. Это НЕ каприз:
# structured logging позволяет Vector распарсить поля без regex,
# а VictoriaLogs — искать по ним как по полям, а не по подстроке.


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "service": SERVICE,
            "pod": POD,
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        return json.dumps(payload, ensure_ascii=False)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
log = logging.getLogger(SERVICE)
log.setLevel(logging.INFO)
log.addHandler(handler)
log.propagate = False


def log_event(level: str, msg: str, **fields) -> None:
    log.log(getattr(logging, level), msg, extra={"extra_fields": fields})


# ------------------------------------------------------------------- метрики
# ТЕХНИЧЕСКИЕ (RED). Лейблы выбраны осознанно: endpoint — низкая кардинальность.
# Никогда не клади в лейбл account_id или payment_id: получишь взрыв
# кардинальности и убьёшь TSDB. Это классический вопрос на собеседовании.

http_requests_total = Counter(
    "http_requests_total",
    "Всего HTTP-запросов",
    ["method", "endpoint", "status"],
)

http_request_duration = Histogram(
    "http_request_duration_seconds",
    "Длительность обработки HTTP-запроса",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

http_inflight = Gauge(
    "http_requests_inflight",
    "Запросов в обработке прямо сейчас",
)

# БИЗНЕСОВЫЕ. Именно их у тебя просят "проектировать совместно с разработкой".
# Разница принципиальная: технические метрики говорят "сервис жив",
# бизнесовые — "деньги ходят". Сервис может быть 200 OK и при этом
# не проводить ни одного платежа.

payments_total = Counter(
    "payments_total",
    "Количество попыток платежей",
    ["status", "channel"],
)

payment_amount_total = Counter(
    "payment_amount_uzs_total",
    "Суммарный объём платежей в UZS",
    ["channel"],
)

# ------------------------------------------------------------------ состояние
chaos = {
    "error_rate": 0.0,   # доля запросов, отвечающих 500 (0.0 - 1.0)
    "latency_ms": 0,     # искусственная задержка
    "down": False,       # сервис "лежит": всё отдаёт 503
}

app = FastAPI(title="payment-api")
CHANNELS = ["mobile", "atm", "web", "branch"]


@app.middleware("http")
async def observe(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    start = time.perf_counter()
    trace_id = request.headers.get("x-trace-id", uuid.uuid4().hex[:16])
    http_inflight.inc()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        raise
    finally:
        http_inflight.dec()
        elapsed = time.perf_counter() - start
        endpoint = request.scope.get("route").path if request.scope.get("route") else "unknown"
        http_request_duration.labels(request.method, endpoint).observe(elapsed)
        http_requests_total.labels(request.method, endpoint, str(status)).inc()
        log_event(
            "ERROR" if status >= 500 else "INFO",
            "request handled",
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            status=status,
            duration_ms=round(elapsed * 1000, 2),
        )
    return response


def apply_chaos() -> Response | None:
    """Возвращает Response, если хаос требует отказать. Иначе None."""
    if chaos["down"]:
        return Response(content='{"error":"service unavailable"}', status_code=503,
                        media_type="application/json")
    if chaos["latency_ms"]:
        time.sleep(chaos["latency_ms"] / 1000)
    if random.random() < chaos["error_rate"]:
        return Response(content='{"error":"internal error"}', status_code=500,
                        media_type="application/json")
    return None


@app.get("/healthz")
def healthz():
    # liveness: жив ли процесс вообще. Намеренно НЕ учитывает хаос,
    # иначе Kubernetes начнёт перезапускать под во время наших экспериментов.
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    # readiness: готов ли принимать трафик. Вот здесь хаос учитываем.
    if chaos["down"]:
        return Response(content='{"status":"not ready"}', status_code=503,
                        media_type="application/json")
    return {"status": "ready"}


@app.get("/balance/{account_id}")
def balance(account_id: str):
    failed = apply_chaos()
    if failed:
        return failed
    time.sleep(random.uniform(0.005, 0.04))
    return {"account": account_id, "balance": random.randint(10_000, 5_000_000)}


@app.post("/payment")
def payment():
    channel = random.choice(CHANNELS)
    failed = apply_chaos()
    if failed:
        payments_total.labels("failed", channel).inc()
        return failed

    time.sleep(random.uniform(0.02, 0.12))
    amount = random.randint(5_000, 2_000_000)
    payments_total.labels("success", channel).inc()
    payment_amount_total.labels(channel).inc(amount)
    return {"payment_id": uuid.uuid4().hex[:12], "amount": amount, "channel": channel}


@app.post("/chaos")
def set_chaos(error_rate: float = 0.0, latency_ms: int = 0, down: bool = False):
    chaos.update(error_rate=error_rate, latency_ms=latency_ms, down=down)
    log_event("WARNING", "chaos applied", **chaos)
    return chaos


@app.get("/chaos")
def get_chaos():
    return chaos


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# obs-platform

Учебная платформа наблюдаемости: Prometheus, Grafana, Loki, Alertmanager.
Стенд для отработки навыков SRE / Observability Engineer : мониторинг, алертинг, SLI/SLO, разбор инцидентов.

## Структура
```
apps/payment-api/   подопытный сервис (метрики, JSON-логи, управляемый хаос)
apps/loadgen/       генератор фонового трафика
monitoring/         конфигурации стека наблюдаемости
dashboards/         дашборды Grafana как код
docs/runbooks/      инструкции по реагированию на алерты
docs/postmortems/   разборы инцидентов
```

## Быстрый старт
```bash
k3d cluster create obs-lab --agents 2 --k3s-arg "--disable=traefik@server:0"
make build
make deploy
## Доступ

| Сервис | URL |
|---|---|
| Grafana | http://grafana.localtest.me:8080 |
| Prometheus | http://prometheus.localtest.me:8080 |
| Alertmanager | http://alerts.localtest.me:8080 |
| payment-api | http://payment.localtest.me:8080 |

Домен `localtest.me` резолвится в 127.0.0.1 — настройка hosts не требуется.

## Управление хаосом
| Команда | Эффект |
|---|---|
| `make chaos-slow`   | задержка 800 мс на каждый запрос |
| `make chaos-errors` | 30% запросов отвечают 500 |
| `make chaos-down`   | сервис отдаёт 503, readiness падает |
| `make chaos-reset`  | вернуть в норму |

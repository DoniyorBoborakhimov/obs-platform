# Runbook: PaymentApiHighErrorRate

**Алерт:** доля 5xx у payment-api выше 5%
**Severity:** critical
**Влияние на пользователя:** часть платежей не проходит, клиент видит ошибку

---

## 1. Быстрая оценка (2 минуты)

Насколько всё плохо и что именно ломается:

```bash
# Текущая доля ошибок и разбивка по путям
curl -s -G "http://prometheus.localtest.me:8080/api/v1/query" \
  --data-urlencode 'query=sum by (path, status) (rate(http_requests_total{job="payment-api", status=~"5.."}[5m]))' \
  | jq -r '.data.result[] | "\(.metric.path)\t\(.metric.status)\t\(.value[1])"'

# Состояние подов
kubectl -n banking get pods -o wide
```

**Что смотреть:**
- ошибки на всех путях или на одном → одна сломанная ручка или весь сервис
- все поды Running или часть падает → проблема в коде или в инфраструктуре
- RESTARTS растёт → смотри раздел 3

## 2. Найти причину в логах (3 минуты)

Открой Grafana → Explore → Loki, или через дашборд `payment-api/RED`
правый клик на панели Error Ratio → «Показать логи ошибок».

Запрос:
```logql
{namespace="banking", app="payment-api"} | json | status >= 500
```

**Что искать:**
- одинаковый текст ошибки во всех строках → один баг
- разные ошибки → проблема шире (ресурсы, зависимость, сеть)
- `duration_ms` маленький (1-5 мс) → быстрый отказ, до реальной работы
- `duration_ms` большой (>1000) → таймаут зависимости

Взять любой `trace_id` из ошибочной строки и посмотреть весь путь запроса:
```logql
{namespace="banking"} | json | trace_id = "ВСТАВЬ_ID"
```

## 3. Проверить типовые причины

### Поды перезапускаются
```bash
kubectl -n banking describe pod -l app=payment-api | grep -A 8 "Last State"
```
`Reason: OOMKilled`, `Exit Code: 137` → не хватает памяти.
Временно: `kubectl -n banking set resources deploy/payment-api --limits=memory=512Mi`
**Обязательно** внести правку в манифест и закоммитить, иначе следующий деплой откатит.

### Недавно был деплой
```bash
kubectl -n banking rollout history deploy/payment-api
```
Если ошибки начались сразу после выката — откат:
```bash
kubectl -n banking rollout undo deploy/payment-api
kubectl -n banking rollout status deploy/payment-api
```

### Включён режим хаоса (только для стенда)
```bash
curl -s http://payment.localtest.me:8080/chaos | jq
curl -s -X POST "http://payment.localtest.me:8080/chaos?error_rate=0&latency_ms=0&down=false"
```

### Нагрузка выше обычной
```bash
curl -s -G "http://prometheus.localtest.me:8080/api/v1/query" \
  --data-urlencode 'query=sum(rate(http_requests_total{job="payment-api"}[5m]))' | jq -r '.data.result[0].value[1]'
```
Норма ~20 RPS. Если сильно выше — масштабировать:
```bash
kubectl -n banking scale deploy/payment-api --replicas=5
```

## 4. Стабилизация

Порядок действий по убыванию предпочтительности:

1. **Откат релиза**, если ошибки начались после деплоя — самое быстрое и безопасное
2. **Масштабирование**, если причина в нагрузке
3. **Правка лимитов**, если OOMKilled
4. **Перезапуск подов** — крайняя мера, скрывает причину:
```bash
   kubectl -n banking rollout restart deploy/payment-api
```

## 5. Когда эскалировать

Эскалируй **немедленно**, если:
- ошибок больше 50% — сервис фактически лежит
- затронуты деньги: платежи проходят частично или дублируются
- причина не найдена за 15 минут
- откат не помог

Кому: владелец сервиса → тимлид платежей → дежурный архитектор.

## 6. Чего НЕ делать

- Не удалять поды по одному без понимания причины — потеряешь состояние для разбора
- Не поднимать лимиты памяти вслепую при утечке — отодвигает падение, не лечит
- Не отключать алерт вместо починки
- Не менять конфиг напрямую в кластере без записи в git — откатится при следующем деплое

## 7. После инцидента

- Зафиксировать таймлайн: когда началось, когда заметили, когда починили
- Сохранить ссылки на графики с нужным временным диапазоном
- Завести задачу на postmortem, если инцидент длился больше 15 минут
  или затронул деньги

---
*Обновлён: 2026-08-16. Владелец: команда payments.*

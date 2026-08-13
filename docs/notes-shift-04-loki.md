# Смена 4: Loki + Promtail

## Зачем логи, если есть метрики

| | Метрика | Лог |
|---|---|---|
| Отвечает на вопрос | **что** происходит | **почему** это произошло |
| Пример | 9% ошибок, latency 800ms | конкретный запрос, trace_id, текст ошибки |
| Стоимость | дёшево, предагрегировано | дорого, сканирование |
| Хранение 7 дней | мегабайты | гигабайты |
| Годится для алертов | да | только для редких событий |

**Рабочий цикл на инциденте:** метрика показала аномалию → лог объяснил причину.

**Правило:** алерты и SLO — на метриках. Разбор инцидента — на логах.

Практический пример со смены: при `error_rate=0.3` латентность на дашборде
не изменилась. Логи показали `duration_ms: 1.02` у ошибочных запросов —
код отдаёт 500 сразу, не выполняя работу. Быстрый отказ не растит латентность.

---

## Loki vs Elasticsearch

**Elasticsearch** индексирует содержимое логов (инвертированный индекс по словам).
→ мощный полнотекстовый поиск, но индекс часто больше самих данных, дорого.

**Loki** индексирует ТОЛЬКО лейблы, тело складывает в сжатые чанки.
→ индекс крошечный, поиск по тексту = последовательное сканирование
отобранных по лейблам чанков. Медленнее, но на порядок дешевле.

Отсюда девиз Loki «как Prometheus, но для логов» — буквально та же модель данных.

---

## Режимы развёртывания

| Режим | Устройство | Когда |
|---|---|---|
| Monolithic (SingleBinary) | всё в одном процессе | до ~20 ГБ/день, стенды |
| Simple Scalable | три роли: read, write, backend | до нескольких ТБ/день |
| Distributed | distributor, ingester, querier, compactor... | огромные объёмы |

Смысл разделения read/write: пишут постоянно и равномерно, читают редко
и всплесками (во время инцидента). Роли масштабируются независимо.

**Хранилище:** в проде Loki пишет чанки в объектное хранилище (S3, GCS, MinIO).
Компоненты становятся stateless, данные лежат в дешёвом storage.
На стенде — filesystem.

---

## Кардинальность лейблов — ГЛАВНАЯ ловушка Loki

Каждая уникальная комбинация лейблов = отдельный **поток (stream)**.
Loki хранит для каждого потока свои чанки.

| Поле | Кардинальность | Решение |
|---|---|---|
| service, namespace | единицы | лейбл |
| level | 4-5 значений | лейбл |
| pod | ~3, но меняется при каждом деплое | лейбл с оговоркой |
| method, status, path | единицы-десятки | тело |
| **trace_id** | уникален для каждого запроса | **тело, категорически** |
| duration_ms | непрерывное число | тело |

Положишь trace_id в лейбл → поток на каждый запрос → десятки тысяч потоков в час
→ Loki создаёт крошечные чанки, забивает индекс и ложится.

Симптомы в проде: Loki ест память, запросы тормозят,
ошибки `maximum active stream limit exceeded`.

**Promtail сам добавляет много лейблов:** filename, node_name, instance, job,
component, stream, service_name. `filename` содержит UID пода → меняется
при каждом пересоздании! Лишние выбрасываются стадией `labeldrop`.

**Важно:** на стенде проблема НЕ видна (9 потоков). Она приходит с масштабом
и временем: 20 деплоев × 30 сервисов × retention = десятки тысяч потоков.
Поэтому её не ловят на тестовом контуре.

---

## Установка

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm install loki grafana/loki --version 7.3.0 \
  --namespace monitoring -f monitoring/loki/values.yaml --wait

helm install promtail grafana/promtail \
  --namespace monitoring -f monitoring/promtail/values.yaml --wait
```

### Ключевые параметры values.yaml Loki

```yaml
deploymentMode: SingleBinary
loki:
  auth_enabled: false        # Loki мультитенантный, требует X-Scope-OrgID.
                             # На стенде выключаем, в проде с командами — включают
  storage:
    type: filesystem         # в проде здесь s3
  schemaConfig:
    configs:
      - from: "2024-04-01"   # schemaConfig хранит ИСТОРИЮ схем:
        store: tsdb          # старые данные читаются по старой схеме,
        schema: v13          # новые пишутся по новой.
                             # Частое место ошибок при апгрейде Loki
chunksCache: {enabled: false}   # по умолчанию поднимает memcached на гигабайты
resultsCache: {enabled: false}
```

### Promtail: pipelineStages

```yaml
config:
  clients:
    - url: http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push
  snippets:
    pipelineStages:
      - cri: {}          # снять обёртку containerd (timestamp, stdout/stderr, флаг)
      - json:            # распарсить наш JSON, извлечь поля в переменные
          expressions:
            level: level
            service: service
      - labels:          # превратить переменные в лейблы Loki
          level:         # ТОЛЬКО низкокардинальные!
```

**Как логи попадают в Loki:**
контейнер пишет в stdout → containerd складывает в `/var/log/pods/...` на ноде
→ Promtail (DaemonSet, по поду на ноду) читает файлы → шлёт в Loki.

---

## Архитектура: что развернулось

```
loki-0                 StatefulSet (2/2: loki + sidecar правил алертов)
storage-loki-0         PVC 10Gi, Bound, storageClass local-path
loki                   ClusterIP — точка входа
loki-headless          без IP — обращение к подам поимённо
loki-memberlist :7946  gossip-протокол: компоненты находят друг друга
                       и обменивают состояние кольца БЕЗ etcd/Consul
promtail-xxxxx x3      DaemonSet, по поду на каждую ноду
```

---

## LogQL

### Структура запроса

```
{селектор по лейблам} | фильтры по содержимому | парсер | фильтры по полям
```

Селектор в фигурных скобках **обязателен** — Loki должен сначала понять,
какие потоки читать. Прямое следствие архитектуры.

### Фильтры по содержимому

| Оператор | Значение |
|---|---|
| `\|=` | строка содержит |
| `!=` | не содержит |
| `\|~` | подходит под регулярку |
| `!~` | не подходит под регулярку |

### Примеры

```logql
# базовый селектор
{namespace="banking", app="payment-api"}

# фильтр по подстроке
{namespace="banking", app="payment-api"} |= "payment"

# парсинг JSON + фильтр по полю (поле НЕ в индексе, но искать можно)
{namespace="banking", app="payment-api"} | json | duration_ms > 100

# только ошибки
{namespace="banking", app="payment-api"} | json | status >= 500

# ПРОСЛЕДИТЬ ОДИН ЗАПРОС через все сервисы
{namespace="banking"} | json | trace_id = "9c772d613248485e"
```

### Агрегация: логи → метрики

Синтаксис намеренно скопирован с PromQL.

```logql
# RPS ошибок из логов
sum(rate({namespace="banking", app="payment-api"} | json | status >= 500 [1m]))

# количество логов по уровням
sum by (level) (count_over_time({namespace="banking", app="payment-api"}[1m]))
```

### Правило производительности

Grafana показывает оценку: `This query will process approximately 4.3 MiB`.

**Сначала максимально сузь селектор по лейблам, потом фильтруй по содержимому.**

```logql
{namespace="banking"} | json | duration_ms > 100        # плохо: сканирует всё
{namespace="banking", app="payment-api", level="ERROR"} # хорошо: только нужные потоки
```

На стенде разница в мегабайтах, в проде — секунды против минут во время инцидента.

---

## Подключение Loki к Grafana

```bash
curl -s -u admin:admin -X POST "http://localhost:3000/api/datasources" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Loki",
    "type": "loki",
    "uid": "loki",
    "url": "http://loki.monitoring.svc.cluster.local:3100",
    "access": "proxy"
  }' | jq
```

`"access": "proxy"` — запросы идут через бэкенд Grafana, а не из браузера.
Вариант `direct` устарел: требует доступности источника из браузера
каждого пользователя и ломает модель безопасности.

---

## API Loki для отладки

```bash
kubectl -n monitoring port-forward svc/loki 3100:3100 &

# какие лейблы вообще есть
curl -s "http://localhost:3100/loki/api/v1/labels" | jq

# значения конкретного лейбла
curl -s "http://localhost:3100/loki/api/v1/label/level/values" | jq

# сколько потоков в namespace — проверка кардинальности
curl -s -G "http://localhost:3100/loki/api/v1/series" \
  --data-urlencode 'match[]={namespace="banking"}' | jq '.data | length'
```

---

## Trace ID и Span ID

**Trace ID** — создаётся ОДИН раз на границе системы (ingress, API gateway,
первый сервис). Живёт весь путь запроса.

**Span ID** — создаётся заново в каждом сервисе и на каждой операции.
Связываются в дерево через `parent_span_id`.

```
trace_id = abc123 (один на весь запрос)
  span A: ingress          (parent: нет)
    span B: payment-api    (parent: A)
      span C: запрос в БД  (parent: B)
      span D: вызов auth   (parent: B)
```

**Передача** — заголовок W3C Trace Context (общий стандарт;
раньше был зоопарк: B3 от Zipkin, X-Ray от Amazon, свой у Jaeger):

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             ^  ^ trace-id (16 байт)             ^ span-id (8)    ^ flags
             версия
```

**Кто генерирует:**

| Уровень | Что происходит |
|---|---|
| Автоинструментация OTel | библиотека перехватывает HTTP клиент/сервер, всё сама |
| Service mesh | Istio/Linkerd генерируют на sidecar, но приложение всё равно должно ПРОБРАСЫВАТЬ заголовок дальше |
| Руками | читаешь заголовок → в контекст → в логи → в исходящие запросы |

**ГЛАВНАЯ ловушка в проде:** сервис получил trace_id, записал в свой лог,
но не передал в исходящий запрос к следующему сервису → трейс обрывается.
Самая частая причина «почему трассировка не работает».
Проблема всегда в приложении, а не в инфраструктуре.

В нашем payment-api упрощённая версия:
`request.headers.get("x-trace-id", uuid...)` — берём из заголовка,
иначе генерируем свой.

---

## Promtail deprecated → Alloy

Grafana продвигает **Alloy** как замену. Alloy умеет больше:
один агент вместо трёх (метрики + логи + трейсы), свой язык конфигурации,
поддержка OpenTelemetry из коробки.

Promtail проще для понимания и всё ещё повсеместно используется.
План: оставить Promtail работающим, поставить Alloy рядом, сравнить.
В портфолио «мигрировал с Promtail на Alloy» звучит лучше,
чем «поставил Alloy».

---

## Итог смены

- [x] Loki в monolithic-режиме, PVC 10Gi, retention 7d
- [x] Promtail DaemonSet на 3 нодах, парсинг JSON, level в лейблы
- [x] Loki подключён к Grafana как источник данных
- [x] LogQL: селекторы, фильтры, парсер json, агрегация
- [x] Проверено на хаосе: логи показали причину, которую метрика не объяснила
- [ ] labeldrop для лишних лейблов Promtail
- [ ] связка метрика → лог в один клик (derived fields)

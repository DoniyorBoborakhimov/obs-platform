# curl — шпаргалка SRE

Примеры привязаны к нашему стенду. Предполагается, что port-forward подняты:
Grafana :3000, Prometheus :9090, Loki :3100, payment-api :8000.

---

## Базовые флаги

| Флаг | Полное имя | Что делает |
|---|---|---|
| `-s` | `--silent` | тихо: без прогресс-бара |
| `-S` | `--show-error` | но ошибки показывать. Обычно вместе: `-sS` |
| `-f` | `--fail` | при HTTP-ошибке вернуть код ошибки, а не сохранить тело ошибки |
| `-L` | `--location` | идти по редиректам |
| `-o file` | `--output` | сохранить в файл |
| `-O` | `--remote-name` | сохранить под именем из URL |
| `-i` | `--include` | показать заголовки ответа вместе с телом |
| `-I` | `--head` | только заголовки, запрос HEAD |
| `-v` | `--verbose` | показать весь диалог: запрос, ответ, TLS |
| `-k` | `--insecure` | не проверять TLS-сертификат (осторожно!) |
| `-X` | `--request` | метод: GET, POST, PUT, DELETE |
| `-H` | `--header` | добавить заголовок |
| `-d` | `--data` | тело запроса (автоматически делает POST) |
| `-u` | `--user` | HTTP Basic Auth: `-u login:password` |
| `-G` | `--get` | отправить данные из `-d` как query-параметры |
| `--data-urlencode` | | закодировать параметр для URL |
| `-w` | `--write-out` | вывести метрики запроса после ответа |
| `--max-time` | | общий таймаут в секундах |
| `--connect-timeout` | | таймаут только на установку соединения |
| `-c` / `-b` | cookie jar | сохранить / отправить cookies |
| `--resolve` | | подменить DNS: `--resolve host:port:IP` |

Комбинация `-fsSL` — стандарт для скачивания скриптов:
fail при ошибке, тихо, но ошибки показать, идти по редиректам.

---

## Диагностика

### Куда ушло время

```bash
curl -s -o /dev/null -w "\
dns:      %{time_namelookup}s
connect:  %{time_connect}s
tls:      %{time_appconnect}s
ttfb:     %{time_starttransfer}s
total:    %{time_total}s
code:     %{http_code}
size:     %{size_download} bytes
" http://localhost:8000/payment
```

Как читать разложение:
- большой `time_namelookup` → проблема DNS
- большой разрыв `connect` - `namelookup` → сеть или TCP-хендшейк
- большой `appconnect` - `connect` → TLS-хендшейк, часто медленный OCSP
- большой `starttransfer` - `appconnect` → **само приложение думает** (TTFB)
- `total` - `starttransfer` → долгая передача тела

Это первое, что делают при жалобе «сайт тормозит»: разложить время по фазам.

### Полный диалог с сервером

```bash
curl -v http://localhost:8000/healthz
```

В выводе: `>` — то, что отправил curl, `<` — то, что ответил сервер,
`*` — служебные сообщения (соединение, TLS, сертификаты).

### Только код ответа

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/healthz
```

Удобно в скриптах и health-чеках.

### Проверить конкретный бэкенд, минуя балансировщик

```bash
curl --resolve payment.example.com:443:10.42.1.5 https://payment.example.com/healthz
```

DNS говорит одно, а ты хочешь проверить конкретный под. Заголовок Host
и SNI остаются правильными, соединение идёт на указанный IP.
**Незаменимо, когда «один инстанс из пяти отдаёт ошибки».**

---

## Работа с JSON API

### GET с параметрами

```bash
# параметры прямо в URL
curl -s "http://localhost:9090/api/v1/query?query=up" | jq

# безопаснее: -G + --data-urlencode (сам закодирует пробелы и спецсимволы)
curl -s -G "http://localhost:9090/api/v1/query" \
  --data-urlencode 'query=sum(rate(http_requests_total[5m]))' | jq
```

Второй способ обязателен, когда в запросе есть пробелы, скобки, кавычки —
то есть почти всегда с PromQL и LogQL.

### POST с JSON

```bash
curl -s -X POST "http://localhost:3000/api/datasources" \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d '{"name":"Loki","type":"loki","url":"http://loki:3100","access":"proxy"}' | jq
```

### POST из файла

```bash
curl -s -X POST "http://localhost:3000/api/dashboards/db" \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d @dashboard.json | jq
```

Символ `@` перед именем файла — «взять тело из файла».

### Аутентификация

```bash
# Basic (логин:пароль) — попадает в history и в список процессов!
curl -u admin:admin http://localhost:3000/api/health

# Bearer token — так делают в проде
curl -H "Authorization: Bearer $TOKEN" http://localhost:3000/api/health

# из переменной окружения, чтобы не светить в history
export GRAFANA_TOKEN="glsa_..."
curl -H "Authorization: Bearer $GRAFANA_TOKEN" ...
```

---

## Практика на нашем стенде

### payment-api

```bash
# метрики глазами Prometheus
curl -s localhost:8000/metrics | grep -E "^http_requests_total"

# провести платёж
curl -s -X POST localhost:8000/payment | jq

# запрос с собственным trace_id — потом найдёшь его в Loki
curl -s -H "x-trace-id: my-test-12345" localhost:8000/payment | jq

# включить хаос
curl -s -X POST "localhost:8000/chaos?error_rate=0.3" | jq
curl -s -X POST "localhost:8000/chaos?latency_ms=800" | jq
curl -s -X POST "localhost:8000/chaos?down=true" | jq
curl -s -X POST "localhost:8000/chaos?error_rate=0&latency_ms=0&down=false" | jq

# посмотреть текущее состояние хаоса
curl -s localhost:8000/chaos | jq
```

### Prometheus API

```bash
# мгновенное значение
curl -s -G "http://localhost:9090/api/v1/query" \
  --data-urlencode 'query=up{job="payment-api"}' | jq '.data.result'

# диапазон
curl -s -G "http://localhost:9090/api/v1/query_range" \
  --data-urlencode 'query=sum(rate(http_requests_total{job="payment-api"}[5m]))' \
  --data-urlencode "start=$(date -d '1 hour ago' +%s)" \
  --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=60' | jq '.data.result[0].values | length'

# список активных таргетов
curl -s "http://localhost:9090/api/v1/targets?state=active" \
  | jq '.data.activeTargets[] | {job: .labels.job, health, lastError}'

# сколько временных рядов всего — проверка кардинальности
curl -s -G "http://localhost:9090/api/v1/query" \
  --data-urlencode 'query=prometheus_tsdb_head_series' | jq '.data.result[0].value[1]'

# метрики с самой большой кардинальностью
curl -s "http://localhost:9090/api/v1/status/tsdb" \
  | jq '.data.seriesCountByMetricName[:10]'

# перечитать конфиг без рестарта (если включён --web.enable-lifecycle)
curl -s -X POST http://localhost:9090/-/reload
```

Предпоследняя команда — то, что запускают первой, когда Prometheus начал есть память.

### Loki API

```bash
# какие лейблы есть
curl -s "http://localhost:3100/loki/api/v1/labels" | jq

# значения лейбла
curl -s "http://localhost:3100/loki/api/v1/label/level/values" | jq

# сколько потоков в namespace — проверка кардинальности
curl -s -G "http://localhost:3100/loki/api/v1/series" \
  --data-urlencode 'match[]={namespace="banking"}' | jq '.data | length'

# запрос логов
curl -s -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="banking", app="payment-api"} | json | status >= 500' \
  --data-urlencode "start=$(date -d '10 minutes ago' +%s)000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  --data-urlencode 'limit=5' | jq '.data.result[].values[][1]'

# готовность Loki
curl -s http://localhost:3100/ready
```

Обрати внимание: Loki принимает время в **наносекундах** — отсюда `000000000`
в конце. Prometheus — в секундах. Частая причина «почему запрос ничего не вернул».

### Grafana API

```bash
# здоровье
curl -s http://localhost:3000/api/health | jq

# список источников данных
curl -s -u admin:admin http://localhost:3000/api/datasources \
  | jq '.[] | {name, type, uid}'

# поиск дашбордов
curl -s -u admin:admin "http://localhost:3000/api/search?query=payment" \
  | jq '.[] | {uid, title, type}'

# выгрузить дашборд как JSON (то, что мы делали на смене 3)
curl -s -u admin:admin "http://localhost:3000/api/dashboards/uid/payment-api-red" \
  | jq '.dashboard' > dashboard.json

# папки и права
curl -s -u admin:admin http://localhost:3000/api/folders | jq
curl -s -u admin:admin http://localhost:3000/api/folders/<uid>/permissions | jq
```

---

## Полезные приёмы

### Повторять запрос и смотреть коды

```bash
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{http_code} " localhost:8000/payment
done; echo
```

Быстрый способ увидеть долю ошибок при включённом хаосе.

### Замерить латентность серией запросов

```bash
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "%{time_total}\n" localhost:8000/payment
done | sort -n | awk '{a[NR]=$1} END {print "min", a[1]; print "p50", a[int(NR*0.5)]; print "p95", a[int(NR*0.95)]; print "max", a[NR]}'
```

### Retry при флапающем сервисе

```bash
curl -s --retry 3 --retry-delay 2 --retry-connrefused localhost:8000/healthz
```

### Проверить TLS-сертификат

```bash
curl -vI https://example.com 2>&1 | grep -E "subject|issuer|expire"
```

### Скачать и сразу выполнить (осторожно)

```bash
curl -fsSL https://get.docker.com | sudo sh
```

В проде так **не делают**: пакеты ставят из внутреннего зеркала
с проверенными подписями. Знать разницу нужно —
про безопасность цепочки поставок спрашивают на собеседованиях.

---

## Что не забыть

- `-G` + `--data-urlencode` для любых запросов с PromQL/LogQL — иначе
  сломается на первой же скобке
- Loki хочет время в **наносекундах**, Prometheus — в **секундах**
- `-u login:password` попадает в `~/.bash_history` и в `ps aux`.
  В проде — токен из переменной окружения
- `-w "%{time_*}"` — первое, что запускают при жалобе на медленный сервис
- `--resolve` — когда надо проверить один конкретный бэкенд из пула

# Runbook: PaymentApiDown

**Алерт:** `sum(up{job="payment-api"}) == 0` — ни один под не отвечает на скрейп
**Severity:** critical
**Влияние на пользователя:** платежи не проходят, сервис недоступен полностью

> Отличие от `PaymentApiHighErrorRate`: там сервис **отвечает**, но ошибками.
> Здесь он **не отвечает вообще**. Диагностика идёт по сетевому пути, а не по логам приложения.

---

## 0. Спросить у Prometheus (30 секунд)

Прежде чем лезть в поды — посмотреть, что говорит сам скрейпер.
В поле `lastError` часто сразу написана причина.

```bash
curl -s "http://prometheus.localtest.me:8080/api/v1/targets?state=active" \
  | jq '.data.activeTargets[] | select(.labels.job=="payment-api")
        | {pod: .labels.pod, health, lastError, lastScrape}'
```

| Что в `lastError` | Куда идти |
|---|---|
| `connection refused` | процесс не слушает порт → шаг 1 |
| `context deadline exceeded` | приложение висит или тормозит → шаг 2 |
| `no such host` | проблема DNS → шаг 4 |
| `i/o timeout` | сеть или NetworkPolicy → шаг 5 |
| таргетов вообще нет в выводе | ServiceMonitor или endpoints → шаг 3 |

Проверить, что упало только это, а не половина кластера:

```bash
curl -s "http://prometheus.localtest.me:8080/api/v1/query?query=count(up==0)" \
  | jq -r '.data.result[0].value[1] // "0"'
```

Если число большое — проблема на уровне кластера, а не сервиса. Эскалировать сразу.

---

## 1. Живы ли поды

```bash
kubectl -n banking get pods -l app=payment-api -o wide
```

| STATUS | Что значит | Действие |
|---|---|---|
| `Running` + `1/1` | под жив и готов | → шаг 3 (сеть) |
| `Running` + `0/1` | жив, но readiness не проходит | → шаг 2 |
| `CrashLoopBackOff` | падает сразу после старта | логи ниже |
| `Pending` | не может разместиться на ноде | ресурсы, ниже |
| `ImagePullBackOff` | не скачивается образ | registry, тег |
| `Terminating` (долго) | не завершается | finalizer или зависший процесс |

**CrashLoopBackOff — смотреть логи умершего контейнера:**

```bash
kubectl -n banking logs -l app=payment-api --tail=100 --previous
kubectl -n banking describe pod -l app=payment-api | grep -A 8 "Last State"
```

Коды выхода:
- `137` — OOMKilled, не хватило памяти
- `143` — SIGTERM, не успел завершиться за grace period
- `1`, `2` — ошибка приложения, причина в логах

**Pending — смотреть, почему планировщик не разместил:**

```bash
kubectl -n banking describe pod -l app=payment-api | grep -A 10 Events
kubectl top nodes
kubectl describe node k3d-obs-lab-agent-0 | grep -A 6 "Allocated resources"
```

Типичное: `Insufficient cpu`, `Insufficient memory`, `node(s) had untolerated taint`.

---

## 2. Readiness не проходит (`0/1 Running`)

Под жив, но Kubernetes считает его неготовым и убрал из Service.

```bash
kubectl -n banking describe pod -l app=payment-api | grep -B 2 -A 6 "Readiness"
kubectl -n banking get events --sort-by='.lastTimestamp' | grep -i readiness | tail -10
```

Проверить пробу вручную изнутри кластера:

```bash
POD_IP=$(kubectl -n banking get pod -l app=payment-api -o jsonpath='{.items[0].status.podIP}')
kubectl -n banking run rb-test --rm -it --restart=Never --image=curlimages/curl -- \
  curl -sS -w "\ncode=%{http_code} time=%{time_total}s\n" --max-time 5 "http://$POD_IP:8000/readyz"
```

Частые причины: приложение долго стартует (мал `initialDelaySeconds`), недоступна зависимость,
включён режим отказа.

**На стенде проверить хаос:**

```bash
curl -s http://payment.localtest.me:8080/chaos | jq
# сброс:
curl -s -X POST "http://payment.localtest.me:8080/chaos?error_rate=0&latency_ms=0&down=false" | jq
```

---

## 3. Service видит поды?

```bash
kubectl -n banking get endpoints payment-api
kubectl -n banking get endpointslices -l kubernetes.io/service-name=payment-api
```

**Есть IP-адреса** → связь на месте, → шаг 4.

**`<none>` или пусто** → Service не находит поды. Сравнить селектор и лейблы:

```bash
kubectl -n banking get svc payment-api -o jsonpath='{.spec.selector}'; echo
kubectl -n banking get pods -l app=payment-api --show-labels
```

Должны совпадать символ в символ. Если поды `0/1 Ready` — endpoints пустые именно поэтому,
это следствие шага 2, а не отдельная проблема.

---

## 4. DNS внутри кластера

```bash
kubectl -n banking run dns-test --rm -it --restart=Never --image=busybox:1.36 -- \
  nslookup payment-api.banking.svc.cluster.local
```

**Ошибка резолвинга** → проверить CoreDNS:

```bash
kubectl -n kube-system get pods -l k8s-app=kube-dns
kubectl -n kube-system logs -l k8s-app=kube-dns --tail=50
```

Если CoreDNS лежит — сломано полкластера, эскалировать немедленно.

---

## 5. Порт метрик доступен?

Имитируем запрос Prometheus напрямую к поду:

```bash
POD_IP=$(kubectl -n banking get pod -l app=payment-api -o jsonpath='{.items[0].status.podIP}')
kubectl -n banking run net-test --rm -it --restart=Never --image=curlimages/curl -- \
  curl -sS -o /dev/null -w "code=%{http_code} time=%{time_total}s\n" --max-time 5 "http://$POD_IP:8000/metrics"
```

| Результат | Причина |
|---|---|
| `code=200` | под в порядке → проблема в ServiceMonitor или в самом Prometheus |
| timeout | трафик блокируется → NetworkPolicy |
| `connection refused` | процесс не слушает порт |

**Если 200, а таргета нет** — проверить ServiceMonitor:

```bash
kubectl -n monitoring get servicemonitor payment-api -o yaml
```

Сверить: `namespaceSelector` указывает на `banking`, `selector.matchLabels` совпадает
с лейблами Service, `port` — это **имя** порта (`http`), а не номер.

**Если timeout** — проверить сетевые политики:

```bash
kubectl -n banking get networkpolicy
```

---

## Стабилизация

По убыванию предпочтительности:

1. **Откат релиза**, если недоступность началась после деплоя:
   ```bash
   kubectl -n banking rollout history deploy/payment-api
   kubectl -n banking rollout undo deploy/payment-api
   kubectl -n banking rollout status deploy/payment-api --timeout=3m
   ```

2. **Освободить ресурсы**, если поды Pending — снять некритичные нагрузки с ноды.

3. **Поднять лимит памяти**, если OOMKilled:
   ```bash
   kubectl -n banking set resources deploy/payment-api --limits=memory=512Mi
   ```
   Обязательно внести правку в `apps/payment-api/k8s/deployment.yaml` и закоммитить,
   иначе следующий деплой откатит изменение.

4. **Перезапуск** — крайняя мера, скрывает причину:
   ```bash
   kubectl -n banking rollout restart deploy/payment-api
   ```
   Перед этим сохранить логи и describe для последующего разбора.

---

## Когда эскалировать

Немедленно, если:
- недоступность длится более 5 минут
- `count(up==0)` больше 3 — проблема шире одного сервиса
- CoreDNS или ноды в состоянии NotReady
- причина не найдена за 10 минут
- откат не помог

Кому: владелец сервиса → тимлид платежей → дежурный по инфраструктуре.

---

## Чего НЕ делать

- **Не удалять Deployment** — потеряешь конфигурацию, восстанавливать дольше
- **Не ослаблять readinessProbe**, чтобы «прошла» — скроешь настоящую проблему
  и пустишь трафик на неработающие поды
- **Не масштабировать**, если поды Pending — усугубишь нехватку ресурсов
- **Не перезапускать до сбора диагностики** — потеряешь состояние, разбирать будет нечего
- **Не править конфиг напрямую в кластере** без записи в git — откатится при деплое

---

## После инцидента

Зафиксировать для postmortem:

```bash
# таймлайн: когда алерт перешёл в firing
curl -s -G "http://prometheus.localtest.me:8080/api/v1/query_range" \
  --data-urlencode 'query=ALERTS{alertname="PaymentApiDown"}' \
  --data-urlencode "start=$(date -d '3 hours ago' +%s)" \
  --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=60' | jq '.data.result[].values[0]'

# события кластера за период
kubectl -n banking get events --sort-by='.lastTimestamp' | tail -30
```

Завести postmortem, если инцидент длился более 15 минут или затронул платежи.

---
*Обновлён: 2026-08-16. Владелец: команда payments.*
*Проверен на стенде obs-lab. Namespace: banking. Service: payment-api, порт 8000.*

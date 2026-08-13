Prometheus
Ставим Prometheus в кластер через Helm, настраиваем service discovery,
учимся читать метрики через PromQL.
Шаг 1. Подключение Helm-репозитория

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

- helm repo add — прописать адрес репозитория чартов (аналог apt sources)
- helm repo update — скачать индекс пакетов (аналог apt update)
- CHART VERSION — версия набора шаблонов
- APP VERSION — версия приложения внутри чарта
- В проде версию чарта фиксируем явно ради воспроизводимости
Шаг 2. Изучение чарта

helm show chart <chart> --version X.Y.Z    # метаданные
helm show values <chart> --version X.Y.Z   # все настройки (5982 строки!)

kube-prometheus-stack включает:
- Prometheus Operator (управляет Prometheus через CRD)
- kube-state-metrics — метрики состояния объектов k8s
- node-exporter — метрики железа нод
- Grafana
- Alertmanager

node-exporter = железо. kube-state-metrics = объекты k8s. Разные слои.
Правило: перед установкой в кластер — посмотри, что внутри чарта.
Шаг 3. Ключевые параметры чарта

prometheus.prometheusSpec:
  retention: 10d          # срок хранения метрик. Для года нужен Thanos/Mimir/VM
  scrapeInterval: ""      # пусто = глобальный дефолт 30s
  serviceMonitorSelectorNilUsesHelmValues: true
      # по умолчанию подбирает только ServiceMonitor с лейблом release=<релиз>
     
Prometheus Operator: конфиг prometheus.yml не правим руками.
Создаём объекты k8s (ServiceMonitor, PodMonitor, PrometheusRule),
оператор сам генерирует конфиг и перезагружает Prometheus.
Профит: разработчики сами кладут ServiceMonitor рядом со своим приложением.
Шаг 4: пишем свой values.yaml

Теперь создаём свой файл настроек — только то, что отличается от дефолта.
Шаг 5. Сухой прогон (helm template)

helm template <release> <chart> --version X -n <ns> -f values.yaml > out.yaml
grep "^kind:" out.yaml | sort | uniq -c | sort -rn

Правило: перед установкой смотрим, ЧТО именно будет создано.
helm template ничего не применяет — только рендерит шаблоны.

Что создаётся (105 объектов):
- PrometheusRule x30 — готовые алерты от сообщества
- ServiceMonitor x9 — таргеты для сбора (нашего payment-api там НЕТ)
- Prometheus, Alertmanager — CRD, за ними следит оператор
- DaemonSet — node-exporter, по поду на каждую ноду
- ClusterRole/Binding — RBAC: Prometheus читает API кластера
- Validating/MutatingWebhook — проверка корректности CRD при apply

Триада RBAC: ServiceAccount -> ClusterRole -> ClusterRoleBinding
Prometheus CRD:
- image: prometheus:v3.13.2-distroless — distroless = нет shell внутри,
  отладка через kubectl debug, а не kubectl exec
- replicas: 1 — SPOF. В проде 2 реплики собирают одно и то же,
  Alertmanager дедуплицирует алерты
- shards — деление таргетов между экземплярами при большом объёме
- Масштабирование: вертикально -> шарды/федерация -> Thanos/Mimir/VM
Как Prometheus находит таргеты в k8s

ДВА УРОВНЯ:
1. ServiceMonitor — пишет человек. Намерение: "собирай с Service,
   у которого лейбл X, порт Y, интервал Z". НЕ содержит IP.
2. Список таргетов — считает Prometheus. Ходит в API кластера,
   находит Endpoints, держит список актуальным непрерывно.

Цепочка:
ServiceMonitor -> Operator видит в API -> генерирует prometheus.yml
-> кладёт в Secret -> Prometheus перечитывает -> сам опрашивает API k8s

Отскейлил до 10 реплик -> 10 таргетов, без правки конфига.

Посмотреть сгенерированный конфиг:
kubectl -n monitoring get secret prometheus-<release>-... \
  -o jsonpath='{.data.prometheus\.yaml\.gz}' | base64 -d | gunzip

В UI: Status -> Configuration (итоговый конфиг)
      Status -> Service discovery (включая ОТБРОШЕННЫЕ таргеты — для отладки)
Ловушка: коллизия лейблов

Приложение отдавало лейбл endpoint (путь роута).
Prometheus ставит СВОЙ endpoint (имя порта из Service) и перезаписывает.
Исходный сохраняется как exported_endpoint.

Зарезервированные Prometheus'ом лейблы в k8s: job, instance, namespace,
pod, container, service, endpoint.

Решение:
1) переименовать лейбл в приложении (path/route/handler) — ПРАВИЛЬНО
2) honorLabels: true в ServiceMonitor — отключает защиту целиком,
   годится для экспортеров, не для обычного приложения

Отладка: если метрика "не та", смотри exported_<label>

Grafana
Подключаем Prometheus как источник данных, разбираем готовые дашборды,
строим свой по методологии RED, храним дашборды как код в git.
Разбор рестартов

kubectl describe pod <pod> | grep -A8 "Last State"

Exit Code:
  0   — штатное завершение
  137 — OOMKilled (128+9, SIGKILL). Почти всегда память
  143 — SIGTERM, не успел завершиться в grace period
  255 — Unknown, часто артефакт остановки ноды снаружи

Признаки настоящей проблемы (а не штатной остановки):
  - растущий Restart Count
  - CrashLoopBackOff
  - Finished в момент, когда никто ничего не трогал

kubectl logs <pod> --previous    # логи УМЕРШЕГО контейнера
kubectl get events --sort-by='.lastTimestamp'
DNS в Kubernetes

Полное имя: <service>.<namespace>.svc.cluster.local
Сокращения:
  payment-api                       — из того же namespace
  payment-api.banking               — из другого namespace
  payment-api.banking.svc.cluster.local — полностью

Резолвит CoreDNS (под в kube-system).
ClusterIP — виртуальный IP, за ним правила iptables/eBPF на каждой ноде.
Процесса-балансировщика по этому адресу НЕТ.

Правило: в конфигах ВСЕГДА DNS-имя сервиса, никогда IP пода.
Под пересоздался -> IP другой, имя Service то же.

Loki
Ставим Loki + Promtail, собираем JSON-логи payment-api,
учимся LogQL, связываем логи с метриками в Grafana.

Provisioned-дашборды нельзя править через UI

Симптом: правишь в UI, жмёшь Save, но изменения не сохраняются.
Причина: дашборд провижененный, Grafana защищает его от правок.
Проверка: GET /api/dashboards/uid/<uid> -> .meta.provisioned == true

Единственный правильный путь изменения:
1. правишь JSON в git (руками или через jq)
2. пересобираешь ConfigMap
3. kubectl apply
4. sidecar подхватывает за ~30 сек

Это не неудобство, а СМЫСЛ подхода: источник истины — git, не база Grafana.

Приём для правки JSON точечно:
jq --argjson link "$LINK" \
  '(.panels[] | select(.title == "Error Ratio") | .fieldConfig.defaults.links) = $link' \
  file.json

Где искать ссылки в JSON дашборда:
  panel.links                        — кнопки в заголовке панели
  panel.fieldConfig.defaults.links   — data links (клик по графику)

Поиск пути до значения в незнакомом JSON:
jq 'paths(type=="string" and test("текст"))' file.json

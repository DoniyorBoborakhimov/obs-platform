# ---------- ingress-nginx ----------
resource "helm_release" "ingress_nginx" {
  name       = "ingress-nginx"
  repository = "https://kubernetes.github.io/ingress-nginx"
  chart      = "ingress-nginx"
  version    = var.ingress_nginx_version

  namespace        = "ingress-nginx"
  create_namespace = true

  values = [file("${path.module}/../monitoring/ingress/values.yaml")]

  wait    = true
  timeout = 600
}

# ---------- kube-prometheus-stack ----------
resource "helm_release" "kube_prometheus_stack" {
  name       = "kube-prom"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = var.kube_prometheus_stack_version

  namespace        = var.monitoring_namespace
  create_namespace = true

  values = [file("${path.module}/../monitoring/prometheus/values.yaml")]

  # пароль передаём отдельно, а не через values в git
  set_sensitive {
    name  = "grafana.adminPassword"
    value = var.grafana_admin_password
  }

  wait    = true
  timeout = 900
}

# ---------- Loki ----------
resource "helm_release" "loki" {
  name       = "loki"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "loki"
  version    = var.loki_version

  namespace = var.monitoring_namespace

  values = [file("${path.module}/../monitoring/loki/values.yaml")]

  wait    = true
  timeout = 600

  depends_on = [helm_release.kube_prometheus_stack]
}

# ---------- Promtail ----------
resource "helm_release" "promtail" {
  name       = "promtail"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "promtail"

  namespace = var.monitoring_namespace

  values = [file("${path.module}/../monitoring/promtail/values.yaml")]

  wait    = true
  timeout = 600

  depends_on = [helm_release.loki]
}

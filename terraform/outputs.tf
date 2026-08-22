output "grafana_url" {
  description = "Адрес Grafana"
  value       = "http://grafana.localtest.me:8080"
}

output "prometheus_url" {
  value = "http://prometheus.localtest.me:8080"
}

output "alertmanager_url" {
  value = "http://alerts.localtest.me:8080"
}

output "argocd_url" {
  value = "http://argocd.localtest.me:8080"
}

output "installed_charts" {
  description = "Установленные Helm-релизы и их версии"
  value = {
    ingress_nginx         = helm_release.ingress_nginx.version
    kube_prometheus_stack = helm_release.kube_prometheus_stack.version
    loki                  = helm_release.loki.version
    promtail              = helm_release.promtail.version
  }
}

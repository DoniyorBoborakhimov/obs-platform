variable "cluster_name" {
  description = "Имя кластера k3d"
  type        = string
  default     = "obs-lab"
}

variable "monitoring_namespace" {
  description = "Namespace для стека наблюдаемости"
  type        = string
  default     = "monitoring"
}

variable "kube_prometheus_stack_version" {
  description = "Версия чарта kube-prometheus-stack"
  type        = string
  default     = "88.1.5"
}

variable "loki_version" {
  description = "Версия чарта Loki"
  type        = string
  default     = "7.3.0"
}

variable "ingress_nginx_version" {
  description = "Версия чарта ingress-nginx"
  type        = string
  default     = "4.15.1"
}

variable "grafana_admin_password" {
  description = "Пароль администратора Grafana"
  type        = string
  sensitive   = true
  default     = "admin"
}

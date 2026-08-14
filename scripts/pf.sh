#!/usr/bin/env bash
pkill -f "kubectl.*port-forward" 2>/dev/null
kubectl -n monitoring port-forward svc/kube-prom-grafana 3000:80 >/dev/null 2>&1 &
kubectl -n monitoring port-forward svc/kube-prom-kube-prometheus-prometheus 9090:9090 >/dev/null 2>&1 &
kubectl -n monitoring port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
kubectl -n banking port-forward svc/payment-api 8000:8000 >/dev/null 2>&1 &
sleep 2
echo "Grafana    http://localhost:3000"
echo "Prometheus http://localhost:9090"
echo "Loki       http://localhost:3100"
echo "payment    http://localhost:8000"

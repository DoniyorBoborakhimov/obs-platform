CLUSTER := obs-lab
APP     := payment-api
TAG     := 0.1.0

.PHONY: build deploy logs chaos-reset chaos-slow chaos-errors chaos-down port

build:
	docker build -t $(APP):$(TAG) apps/payment-api
	k3d image import $(APP):$(TAG) -c $(CLUSTER)

deploy:
	kubectl apply -f apps/payment-api/k8s/namespace.yaml
	kubectl apply -f apps/payment-api/k8s/
	kubectl apply -f apps/loadgen/k8s-loadgen.yaml
	kubectl -n banking rollout status deploy/$(APP)

redeploy: build
	kubectl -n banking rollout restart deploy/$(APP)
	kubectl -n banking rollout status deploy/$(APP)

port:
	kubectl -n banking port-forward svc/$(APP) 8000:8000

logs:
	kubectl -n banking logs -l app=$(APP) -f --tail=20

chaos-reset:
	curl -sX POST "localhost:8000/chaos?error_rate=0&latency_ms=0&down=false" | jq
chaos-slow:
	curl -sX POST "localhost:8000/chaos?latency_ms=800" | jq
chaos-errors:
	curl -sX POST "localhost:8000/chaos?error_rate=0.3" | jq
chaos-down:
	curl -sX POST "localhost:8000/chaos?down=true" | jq

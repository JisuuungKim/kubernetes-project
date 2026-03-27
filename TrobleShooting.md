# Troubleshooting

이 문서는 프로젝트 구현과 검증 과정에서 실제로 발생했던 문제와 원인, 해결 방법을 정리한 기록입니다.

## 1. Ingress 주소로 접근했는데 응답이 멈춤

### 증상

- `/etc/hosts`에 `192.168.49.2 traffic.local` 추가
- `curl http://traffic.local/api/notice`
- 연결이 열린 뒤 응답 없이 멈춤

### 원인

로컬 macOS + Minikube + Docker driver 환경에서는 Ingress Controller의 IP가 보이더라도, 호스트에서 직접 `192.168.49.2:80`으로 접근하는 방식이 안정적으로 동작하지 않았습니다.

이 환경에서는 `minikube service ingress-nginx-controller -n ingress-nginx --url`가 띄우는 로컬 프록시를 통해서만 정상 연결되는 경우가 있습니다.

### 해결

- 초기 확인은 `minikube service ingress-nginx-controller -n ingress-nginx --url`로 수행
- 실제 부하테스트는 Ingress 경유 대신 `kubectl port-forward svc/traffic-app 8000:8000` 방식으로 전환

### 정리

Ingress 자체 문제라기보다 로컬 Minikube 네트워크 접근 방식 문제였습니다. 그래서 부하테스트는 Ingress 대신 Service 포트포워드 방식으로 고정했습니다.

## 2. `traffic.local`이 안 뜬 원인은 Ingress가 아니라 이미지 Pull 실패였음

### 증상

- Ingress 설정은 있어 보였지만 `/api/health` 접근이 안 됨
- `kubectl get pods` 확인 시 `traffic-app`, `traffic-counter-api`, `meme-content-api`가 `ImagePullBackOff`

### 원인

Deployment 이미지가 `harbor.example.com/...`로 설정돼 있었고, 로컬 Minikube 환경에서는 해당 레지스트리 주소를 실제로 당길 수 없었습니다.

즉 서비스가 안 뜬 원인은 Ingress가 아니라 애플리케이션 Pod가 생성되지 못한 것이었습니다.

### 해결

- 로컬 테스트용 Deployment 이미지를 `traffic-app:latest`로 변경
- 로컬에서 이미지 빌드
- Minikube에 이미지 반영 후 재배포

초기에는 아래 방식으로 시도했습니다.

```bash
docker build -t traffic-app:latest .
minikube image load traffic-app:latest
kubectl apply -f k8s/
```

이후 더 안정적인 방식으로 아래 절차를 기본으로 바꿨습니다.

```bash
minikube image build -t traffic-app:latest .
kubectl apply -f k8s/
```

### 정리

로컬 Minikube 검증에서는 외부 Harbor 이미지를 직접 참조하지 말고, Minikube 내부에 직접 이미지를 빌드하는 방식이 가장 안전했습니다.

## 3. `metrics-server`가 없어 HPA가 동작하지 않음

### 증상

- `kubectl get hpa traffic-app`에서 `cpu: <unknown>/50%`
- `kubectl top pods` 실행 시 `Metrics API not available`
- 부하를 줘도 HPA가 scale out 하지 않음

### 원인

Minikube에서 `metrics-server` addon이 비활성화 상태였습니다.

초기 상태:

- `minikube addons list | grep metrics-server`
- 결과: `disabled`

### 해결

```bash
minikube addons enable metrics-server
kubectl get apiservices | grep metrics
kubectl top nodes
kubectl top pods -l app=traffic-app
```

활성화 직후에는 `MissingEndpoints`, `no metrics to serve`가 잠시 보일 수 있습니다. `metric-resolution=60s` 때문에 첫 수집까지 시간이 필요했습니다.

정상화 후 상태:

- `v1beta1.metrics.k8s.io ... True`
- `kubectl top nodes` 정상 출력
- `kubectl top pods` 정상 출력

### 정리

HPA 검증 전에는 반드시 `kubectl top nodes`, `kubectl top pods`가 정상적으로 동작하는지 먼저 확인해야 합니다.

## 4. `TEST_TARGET=counter` 부하테스트가 100% 실패, Redis hit도 0 증가

### 증상

`TEST_TARGET=counter ./scripts/run_portforward_load_test.sh` 실행 시:

- 성공률 `0.00%`
- 상태코드 `404`
- Redis 카운터 증가량 `0`

리포트 예시:

- `Status codes: {'404': 51836}`
- `Counter hits before/after: 84632 -> 84632`

### 원인

코드에는 `/notice/track` 엔드포인트가 추가되어 있었지만, 클러스터의 `traffic-app` Pod는 예전 이미지를 계속 사용하고 있었습니다.

확인 결과:

- 로컬에서 새 코드로 빌드한 이미지 ID와
- 실제 Kubernetes Pod가 사용하던 이미지 ID가 달랐음
- Pod 내부 OpenAPI에도 `/notice/track`, `/notice/message`가 없었음

즉 코드 문제가 아니라, 새 이미지가 실제 Pod에 반영되지 않은 상태였습니다.

### 해결

Minikube 내부에서 직접 이미지를 다시 빌드하고 `traffic-app` Deployment를 롤링 재시작했습니다.

```bash
minikube image build -t traffic-app:latest .
kubectl rollout restart deployment/traffic-app
kubectl rollout status deployment/traffic-app
```

추가로 서비스 뒤에 남아 있던 오래된 Pod가 남아 있으면 404를 계속 낼 수 있어서, old ReplicaSet Pod가 완전히 사라졌는지 확인했습니다.

### 추가 보완

부하테스트 스크립트에 사전 검증을 넣었습니다.

- 실제 테스트 대상 경로에 먼저 1회 요청
- `404`, `502` 등 비정상 상태면 바로 종료
- 의미 없는 부하테스트 결과를 남기지 않도록 방지

### 정리

로컬 이미지 변경 후 `kubectl rollout restart`만으로는 충분하지 않을 수 있습니다. Minikube가 동일 태그의 오래된 이미지를 계속 쓰는 경우가 있어서, `minikube image build`를 기본 절차로 고정했습니다.

## 5. `meme` 전용 테스트에서 "카운터 증가량이 부족하다"는 경고가 뜸

### 증상

`TEST_TARGET=meme` 부하테스트 결과에서:

- 성공률 `100%`
- Redis hit 증가량 `0`
- 그런데 리포트에
  - `카운터 증가량이 성공 응답 수보다 적습니다`
  라는 경고가 출력됨

### 원인

Redis 연동 문제는 아니었습니다.

문제는 리포트 생성기([scripts/render_load_test_report.py](/Users/jisung/Documents/skala/kubernetes/practice/mini-project/scripts/render_load_test_report.py))가 모든 테스트 모드에 대해

- 성공 응답 수 = Redis hit 증가량

이라는 가정을 하고 있었다는 점입니다.

하지만 `meme` 모드는 `/notice/message` 경로를 사용하며, 이 경로는 의도적으로 `traffic-counter-api /hit`를 호출하지 않습니다. 따라서 Redis 카운터가 증가하지 않는 것이 정상입니다.

### 해결

리포트 로직을 테스트 URL 경로 기준으로 바꿨습니다.

- `/notice` -> hit 증가 기대
- `/notice/track` -> hit 증가 기대
- `/notice/message` -> hit 증가 `0` 기대

수정 후에는 `meme` 모드 결과가 아래처럼 해석됩니다.

- `Expected counter hit delta: 0`
- `현재 테스트 경로는 counter를 호출하지 않으므로 Redis 카운터가 증가하지 않는 것이 정상입니다.`

### 정리

Redis 자체는 정상 동작했고, 문제는 리포트 판정 로직의 오탐이었습니다.

## 6. Rolling Update 중 old Pod가 `Completed`로 보임

### 증상

`kubectl get pods -l app=traffic-app -w`로 롤링 업데이트를 관찰할 때 old Pod가 잠깐:

- `Terminating`
- `Completed`

상태로 보였습니다.

### 원인

Deployment Pod가 삭제되기 전에, 컨테이너가 종료 코드 `0`으로 정상 종료되면 watch 출력에서 잠깐 `Completed`로 보일 수 있습니다.

이번 경우 `kubectl get events`를 보면 실제로는:

- `Killing`
- `SuccessfulDelete`
- `Scaled down replica set ...`

순으로 처리되었습니다.

즉 Job처럼 "정상 완료된 작업 Pod" 의미가 아니라, 종료 중인 old Pod가 정상 종료 후 삭제되기 직전에 잠깐 표시된 상태였습니다.

### 해결

별도 조치 필요 없음.

확인 포인트는 다음입니다.

- 새 Pod가 먼저 `Ready`
- old Pod는 그 다음 `Terminating`
- 최종적으로 old ReplicaSet이 `0`
- `kubectl rollout status deployment/traffic-app`가 `successfully rolled out`

### 정리

이번 `Completed`는 비정상 종료가 아니라 정상적인 롤링 업데이트 중 일시적인 상태였습니다.

## 7. HPA가 CPU 5%인데 replica를 바로 줄이지 않음

### 증상

`kubectl get hpa traffic-app -w`에서:

- `cpu: 67%/50%` -> `cpu: 53%/50%` -> `cpu: 5%/50%`

로 내려갔는데도 `REPLICAS=3`이 유지됨

### 원인

Kubernetes HPA는 scale up보다 scale down을 더 보수적으로 처리합니다.

기본적으로 scale down에는 stabilization window가 적용되어, 최근 추천 replica 값을 일정 시간 유지합니다. 따라서 CPU가 잠깐 낮아졌다고 바로 replica를 줄이지 않습니다.

추가로 metrics가 일시적으로 `<unknown>`이 되면 scale down이 더 지연될 수 있습니다.

### 해결

이 동작은 기본적으로 정상입니다. 데모에서 더 빨리 줄어드는 모습을 보여주고 싶다면 HPA에 아래와 같은 behavior를 명시적으로 넣을 수 있습니다.

```yaml
behavior:
  scaleDown:
    stabilizationWindowSeconds: 60
```

### 정리

CPU가 5%로 내려갔는데 replica가 즉시 줄지 않는 것은 HPA 오작동이 아니라 기본 scale-down 정책에 따른 정상 동작입니다.

## 운영 팁

### 1. 코드 수정 후 Minikube 반영 절차

```bash
minikube image build -t traffic-app:latest .
kubectl rollout restart deployment/traffic-app
kubectl rollout status deployment/traffic-app
```

### 2. HPA 검증 전 필수 확인

```bash
kubectl top nodes
kubectl top pods -l app=traffic-app
kubectl get hpa traffic-app
```

### 3. 롤링 업데이트 관찰

```bash
kubectl rollout restart deployment/traffic-app
kubectl rollout status deployment/traffic-app
kubectl get pods -l app=traffic-app -w
kubectl get rs -l app=traffic-app -w
```

### 4. 부하테스트 모드별 실행

```bash
./scripts/run_portforward_load_test.sh
TEST_TARGET=counter ./scripts/run_portforward_load_test.sh
TEST_TARGET=meme ./scripts/run_portforward_load_test.sh
```


# Load Test Report

## Test Configuration
- URL: `http://127.0.0.1:8000/notice`
- Concurrency: `10`
- Duration: `15s`
- Think time: `0.0s`

## Request Result
- Total requests: `5776`
- Success: `5776`
- Failure: `0`
- Success rate: `100.00%`
- RPS: `385.07`
- Latency avg/p95/max: `25.90 / 56.94 / 122.72 ms`
- Status codes: `{'200': 5776}`

## HPA and Pods
- Initial replicas: `2`
- Max current replicas: `2`
- Max desired replicas: `2`
- Max CPU utilization: `4%`
- Max observed traffic-app pod count: `2`
- Max observed traffic-counter-api pod count: `2`
- Max observed meme-content-api pod count: `2`

## Resource Peaks
- traffic-app CPU/Memory peak per pod: `4m / 42.0Mi`
- traffic-counter-api CPU/Memory peak per pod: `3m / 38.0Mi`
- meme-content-api CPU/Memory peak per pod: `3m / 35.0Mi`

## Service Health
- traffic-app readiness after test: `ready`
- counter service status: `ok`
- meme service status: `ok`
- Counter hits before/after: `78856 -> 84632`

## Findings
- 성공률이 100.00%로 안정적입니다.
- 최대 CPU 사용률이 4%라 HPA 임계치 50%를 넘지 않았습니다.
- Redis 카운터는 테스트 동안 5776건 증가했습니다.

## Artifacts
- Raw load summary: `python-loadtest-summary.json`
- HPA samples: `hpa-samples.tsv`
- Pod samples: `pod-samples.tsv`
- Resource samples: `top-samples.tsv`
- traffic-app logs: `traffic-app.logs`
- traffic-counter-api logs: `traffic-counter-api.logs`
- meme-content-api logs: `meme-content-api.logs`

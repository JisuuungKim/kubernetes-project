#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-default}"
LOCAL_PORT="${LOCAL_PORT:-8000}"
TEST_TARGET="${TEST_TARGET:-full}"
CONCURRENCY="${CONCURRENCY:-100}"
DURATION="${DURATION:-120}"
THINK_TIME="${THINK_TIME:-0.0}"
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-5}"
RESULT_DIR="${RESULT_DIR:-loadtest/results}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${RESULT_DIR}/portforward-loadtest-${TIMESTAMP}"
RESULT_FILE="${RUN_DIR}/run.log"
PF_LOG="${RUN_DIR}/portforward.log"
LOAD_LOG="${RUN_DIR}/python-loadtest.log"
LOAD_JSON="${RUN_DIR}/python-loadtest-summary.json"
HPA_SAMPLE_FILE="${RUN_DIR}/hpa-samples.tsv"
POD_SAMPLE_FILE="${RUN_DIR}/pod-samples.tsv"
TOP_SAMPLE_FILE="${RUN_DIR}/top-samples.tsv"

mkdir -p "${RUN_DIR}"

pf_pid=""

case "${TEST_TARGET}" in
  full)
    TARGET_PATH="/notice"
    ;;
  counter)
    TARGET_PATH="/notice/track"
    ;;
  meme)
    TARGET_PATH="/notice/message"
    ;;
  *)
    echo "Unsupported TEST_TARGET: ${TEST_TARGET}" >&2
    echo "Use one of: full, counter, meme" >&2
    exit 1
    ;;
esac

TARGET_URL="http://127.0.0.1:${LOCAL_PORT}${TARGET_PATH}"

cleanup() {
  if [[ -n "${pf_pid}" ]] && kill -0 "${pf_pid}" >/dev/null 2>&1; then
    kill "${pf_pid}" >/dev/null 2>&1 || true
    wait "${pf_pid}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

snapshot_pods() {
  local service="$1"
  local suffix="$2"
  kubectl get pods -n "${NAMESPACE}" -l "app=${service}" -o json > "${RUN_DIR}/${service}-pods-${suffix}.json"
}

capture_http_snapshot() {
  local path="$1"
  local output="$2"
  curl -fsS "http://127.0.0.1:${LOCAL_PORT}${path}" > "${RUN_DIR}/${output}" || true
}

preflight_target() {
  local status
  status="$(curl -sS -o "${RUN_DIR}/target-preflight-response.txt" -w "%{http_code}" "${TARGET_URL}" || true)"
  if [[ "${status}" != "200" ]]; then
    echo "Target endpoint preflight failed: ${TARGET_URL} returned HTTP ${status}" | tee -a "${RESULT_FILE}"
    echo "This usually means the running traffic-app image does not include the requested endpoint yet." | tee -a "${RESULT_FILE}"
    echo "Rebuild and redeploy traffic-app, then retry." | tee -a "${RESULT_FILE}"
    exit 1
  fi
}

sample_state() {
  local now
  now="$(date '+%Y-%m-%d %H:%M:%S')"
  local current desired cpu
  current="$(kubectl get hpa traffic-app -n "${NAMESPACE}" -o jsonpath='{.status.currentReplicas}' 2>/dev/null || true)"
  desired="$(kubectl get hpa traffic-app -n "${NAMESPACE}" -o jsonpath='{.status.desiredReplicas}' 2>/dev/null || true)"
  cpu="$(kubectl get hpa traffic-app -n "${NAMESPACE}" -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null || true)"
  echo -e "${now}\t${current}\t${desired}\t${cpu}" >> "${HPA_SAMPLE_FILE}"

  local service
  for service in traffic-app traffic-counter-api meme-content-api; do
    kubectl get pods -n "${NAMESPACE}" -l "app=${service}" --no-headers 2>/dev/null | \
      awk -v ts="${now}" -v svc="${service}" '{print ts "\t" svc "\t" $1 "\t" $2 "\t" $3 "\t" $4}' >> "${POD_SAMPLE_FILE}" || true
    kubectl top pods -n "${NAMESPACE}" -l "app=${service}" --no-headers 2>/dev/null | \
      awk -v ts="${now}" -v svc="${service}" '{print ts "\t" svc "\t" $1 "\t" $2 "\t" $3}' >> "${TOP_SAMPLE_FILE}" || true
  done
}

echo "== Start port-forward to traffic-app service ==" | tee -a "${RESULT_FILE}"
kubectl port-forward -n "${NAMESPACE}" svc/traffic-app "${LOCAL_PORT}:8000" >"${PF_LOG}" 2>&1 &
pf_pid=$!

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${LOCAL_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:${LOCAL_PORT}/health" >/dev/null 2>&1; then
  echo "traffic-app port-forward did not become ready" | tee -a "${RESULT_FILE}"
  echo "port-forward logs:" | tee -a "${RESULT_FILE}"
  cat "${PF_LOG}" | tee -a "${RESULT_FILE}"
  exit 1
fi

preflight_target

printf "timestamp\tcurrent_replicas\tdesired_replicas\tcpu_utilization\n" > "${HPA_SAMPLE_FILE}"
printf "timestamp\tservice\tpod\tready\tstatus\trestarts\n" > "${POD_SAMPLE_FILE}"
printf "timestamp\tservice\tpod\tcpu\tmemory\n" > "${TOP_SAMPLE_FILE}"

capture_http_snapshot "/health" "app-health-initial.json"
capture_http_snapshot "/stats" "app-stats-initial.json"
snapshot_pods "traffic-app" "initial"
snapshot_pods "traffic-counter-api" "initial"
snapshot_pods "meme-content-api" "initial"

echo "== Initial state ==" | tee -a "${RESULT_FILE}"
kubectl get hpa traffic-app -n "${NAMESPACE}" | tee -a "${RESULT_FILE}"
kubectl get pods -n "${NAMESPACE}" -l app=traffic-app -o wide | tee -a "${RESULT_FILE}"
kubectl top pods -n "${NAMESPACE}" | tee -a "${RESULT_FILE}" || true
sample_state

echo "== Run load test in background ==" | tee -a "${RESULT_FILE}"
echo "Target mode: ${TEST_TARGET} (${TARGET_URL})" | tee -a "${RESULT_FILE}"
python3 loadtest/python_load_test.py \
  --url "${TARGET_URL}" \
  --concurrency "${CONCURRENCY}" \
  --duration "${DURATION}" \
  --think-time "${THINK_TIME}" \
  --json-out "${LOAD_JSON}" > "${LOAD_LOG}" 2>&1 &
load_pid=$!

echo "== Sampling HPA and pod state every ${SAMPLE_INTERVAL}s ==" | tee -a "${RESULT_FILE}"
while kill -0 "${load_pid}" >/dev/null 2>&1; do
  now="$(date '+%Y-%m-%d %H:%M:%S')"
  hpa_status="$(kubectl get hpa traffic-app -n "${NAMESPACE}" -o jsonpath='{.status.currentReplicas}:{.status.desiredReplicas}:{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null || echo 'n/a:n/a:n/a')"
  deploy_status="$(kubectl get deploy traffic-app -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null || echo 'n/a')"
  pod_status="$(kubectl get pods -n "${NAMESPACE}" -l app=traffic-app --no-headers 2>/dev/null | awk '{print $1 ":" $2 ":" $3}' | tr '\n' ' ' || true)"
  echo "[${now}] hpa(current:desired:cpu)=${hpa_status} deploy=${deploy_status} pods=${pod_status}" | tee -a "${RESULT_FILE}"
  sample_state
  sleep "${SAMPLE_INTERVAL}"
done

wait "${load_pid}"

capture_http_snapshot "/health" "app-health-final.json"
capture_http_snapshot "/stats" "app-stats-final.json"
snapshot_pods "traffic-app" "final"
snapshot_pods "traffic-counter-api" "final"
snapshot_pods "meme-content-api" "final"

kubectl logs -n "${NAMESPACE}" -l app=traffic-app --tail=200 > "${RUN_DIR}/traffic-app.logs" 2>&1 || true
kubectl logs -n "${NAMESPACE}" -l app=traffic-counter-api --tail=200 > "${RUN_DIR}/traffic-counter-api.logs" 2>&1 || true
kubectl logs -n "${NAMESPACE}" -l app=meme-content-api --tail=200 > "${RUN_DIR}/meme-content-api.logs" 2>&1 || true

echo "== Final load test summary ==" | tee -a "${RESULT_FILE}"
cat "${LOAD_LOG}" | tee -a "${RESULT_FILE}"

echo "== Final HPA ==" | tee -a "${RESULT_FILE}"
kubectl get hpa traffic-app -n "${NAMESPACE}" | tee -a "${RESULT_FILE}"

echo "== Final pod usage ==" | tee -a "${RESULT_FILE}"
kubectl top pods -n "${NAMESPACE}" | tee -a "${RESULT_FILE}" || true

echo "== Final traffic-app pods ==" | tee -a "${RESULT_FILE}"
kubectl get pods -n "${NAMESPACE}" -l app=traffic-app -o wide | tee -a "${RESULT_FILE}"

echo "== Render report ==" | tee -a "${RESULT_FILE}"
python3 scripts/render_load_test_report.py --run-dir "${RUN_DIR}" | tee -a "${RESULT_FILE}"

echo
echo "Load test finished."
echo "Run log: ${RESULT_FILE}"
echo "Markdown summary: ${RUN_DIR}/summary.md"
echo "Port-forward logs: ${PF_LOG}"

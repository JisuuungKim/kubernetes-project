from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a markdown report from load test artifacts.")
    parser.add_argument("--run-dir", required=True, help="Directory containing load test artifacts.")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_cpu_millicores(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return 0
    if raw.endswith("m"):
        return int(raw[:-1])
    return int(float(raw) * 1000)


def parse_memory_mebibytes(raw: str) -> float:
    raw = raw.strip()
    if not raw:
        return 0.0
    units = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
    }
    for unit, factor in units.items():
        if raw.endswith(unit):
            return float(raw[: -len(unit)]) * factor
    return 0.0


def parse_hpa_samples(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows

    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip():
            continue
        timestamp, current, desired, cpu = line.split("\t")
        rows.append(
            {
                "timestamp": timestamp,
                "current_replicas": int(current) if current else 0,
                "desired_replicas": int(desired) if desired else 0,
                "cpu_utilization": int(cpu) if cpu else 0,
            }
        )
    return rows


def parse_top_samples(path: Path) -> dict[str, dict[str, float]]:
    service_max: dict[str, dict[str, float]] = defaultdict(lambda: {"cpu_m": 0, "memory_mi": 0.0})
    if not path.exists():
        return service_max

    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip():
            continue
        _timestamp, service, _pod, cpu, memory = line.split("\t")
        cpu_m = parse_cpu_millicores(cpu)
        memory_mi = parse_memory_mebibytes(memory)
        service_max[service]["cpu_m"] = max(service_max[service]["cpu_m"], cpu_m)
        service_max[service]["memory_mi"] = max(service_max[service]["memory_mi"], memory_mi)
    return service_max


def parse_pod_counts(path: Path) -> dict[str, int]:
    max_counts: dict[str, int] = defaultdict(int)
    if not path.exists():
        return max_counts

    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip():
            continue
        timestamp, service, pod, _ready, status, _restarts = line.split("\t")
        if status == "Running":
            grouped[(timestamp, service)].add(pod)

    for (_timestamp, service), pods in grouped.items():
        max_counts[service] = max(max_counts[service], len(pods))

    return max_counts


def parse_restart_total(path: Path) -> int:
    payload = read_json(path)
    total = 0
    for item in payload.get("items", []):
        for container in item.get("status", {}).get("containerStatuses", []):
            total += int(container.get("restartCount", 0))
    return total


def extract_counter_hits(stats_payload: dict[str, Any]) -> int:
    return int(stats_payload.get("counter_stats", {}).get("total_hits", 0))


def expected_counter_hits_for_url(url: str, success_count: int) -> int:
    path = urlparse(url).path
    if path in ("/notice", "/notice/track"):
        return success_count
    if path == "/notice/message":
        return 0
    return success_count


def render_report(run_dir: Path) -> str:
    load_summary = read_json(run_dir / "python-loadtest-summary.json")
    initial_stats = read_json(run_dir / "app-stats-initial.json")
    final_stats = read_json(run_dir / "app-stats-final.json")
    final_health = read_json(run_dir / "app-health-final.json")
    hpa_samples = parse_hpa_samples(run_dir / "hpa-samples.tsv")
    top_samples = parse_top_samples(run_dir / "top-samples.tsv")
    max_pod_counts = parse_pod_counts(run_dir / "pod-samples.tsv")

    initial_hits = extract_counter_hits(initial_stats)
    final_hits = extract_counter_hits(final_stats)
    hit_delta = max(0, final_hits - initial_hits)

    totals = load_summary.get("totals", {})
    latency = load_summary.get("latency_ms", {})
    config = load_summary.get("config", {})

    initial_replicas = hpa_samples[0]["current_replicas"] if hpa_samples else 0
    max_current_replicas = max((row["current_replicas"] for row in hpa_samples), default=0)
    max_desired_replicas = max((row["desired_replicas"] for row in hpa_samples), default=0)
    max_cpu_utilization = max((row["cpu_utilization"] for row in hpa_samples), default=0)

    restart_deltas = {}
    for service in ("traffic-app", "traffic-counter-api", "meme-content-api"):
        before = parse_restart_total(run_dir / f"{service}-pods-initial.json")
        after = parse_restart_total(run_dir / f"{service}-pods-final.json")
        restart_deltas[service] = max(0, after - before)

    findings: list[str] = []
    success_rate = float(totals.get("success_rate", 0.0))
    success_count = int(totals.get("success_count", 0))
    expected_hit_delta = expected_counter_hits_for_url(config.get("url", ""), success_count)
    if success_rate < 99.0:
        findings.append(f"- 성공률이 {success_rate:.2f}%라서 에러 원인 점검이 필요합니다.")
    else:
        findings.append(f"- 성공률이 {success_rate:.2f}%로 안정적입니다.")

    if max_current_replicas > initial_replicas:
        findings.append(
            f"- HPA가 `traffic-app` replica를 {initial_replicas} -> {max_current_replicas}까지 확장했습니다."
        )
    elif max_cpu_utilization >= 50:
        findings.append(
            "- CPU는 임계치에 도달했지만 replica 확장이 관찰되지 않았습니다. HPA 이벤트와 stabilization 설정을 확인해야 합니다."
        )
    else:
        findings.append(
            f"- 최대 CPU 사용률이 {max_cpu_utilization}%라 HPA 임계치 50%를 넘지 않았습니다."
        )

    if expected_hit_delta == 0:
        if hit_delta == 0:
            findings.append("- 현재 테스트 경로는 counter를 호출하지 않으므로 Redis 카운터가 증가하지 않는 것이 정상입니다.")
        else:
            findings.append(
                f"- 현재 테스트 경로는 counter를 호출하지 않아야 하는데 Redis 카운터가 {hit_delta}건 증가했습니다. 요청 경로 분리를 다시 확인해야 합니다."
            )
    elif hit_delta < expected_hit_delta:
        findings.append(
            f"- Redis 카운터가 기대치보다 적게 증가했습니다. 기대 `{expected_hit_delta}`건, 실제 `{hit_delta}`건입니다."
        )
    else:
        findings.append(f"- Redis 카운터는 테스트 동안 {hit_delta}건 증가했습니다.")

    for service, delta in restart_deltas.items():
        if delta > 0:
            findings.append(f"- `{service}`에서 컨테이너 재시작이 {delta}회 발생했습니다.")

    ready_status = final_health.get("status", "unknown")
    counter_ready = final_health.get("counter_service", {}).get("status", "unknown")
    meme_ready = final_health.get("meme_service", {}).get("status", "unknown")

    lines = [
        "# Load Test Report",
        "",
        "## Test Configuration",
        f"- URL: `{config.get('url', '')}`",
        f"- Concurrency: `{config.get('concurrency', 0)}`",
        f"- Duration: `{config.get('duration_seconds', 0)}s`",
        f"- Think time: `{config.get('think_time_seconds', 0)}s`",
        "",
        "## Request Result",
        f"- Total requests: `{totals.get('total_requests', 0)}`",
        f"- Success: `{totals.get('success_count', 0)}`",
        f"- Failure: `{totals.get('failure_count', 0)}`",
        f"- Success rate: `{success_rate:.2f}%`",
        f"- RPS: `{totals.get('rps', 0.0):.2f}`",
        f"- Latency avg/p95/max: `{latency.get('avg', 0.0):.2f} / {latency.get('p95', 0.0):.2f} / {latency.get('max', 0.0):.2f} ms`",
        f"- Status codes: `{load_summary.get('status_codes', {})}`",
        "",
        "## HPA and Pods",
        f"- Initial replicas: `{initial_replicas}`",
        f"- Max current replicas: `{max_current_replicas}`",
        f"- Max desired replicas: `{max_desired_replicas}`",
        f"- Max CPU utilization: `{max_cpu_utilization}%`",
        f"- Max observed traffic-app pod count: `{max_pod_counts.get('traffic-app', 0)}`",
        f"- Max observed traffic-counter-api pod count: `{max_pod_counts.get('traffic-counter-api', 0)}`",
        f"- Max observed meme-content-api pod count: `{max_pod_counts.get('meme-content-api', 0)}`",
        "",
        "## Resource Peaks",
        f"- traffic-app CPU/Memory peak per pod: `{top_samples.get('traffic-app', {}).get('cpu_m', 0)}m / {top_samples.get('traffic-app', {}).get('memory_mi', 0.0):.1f}Mi`",
        f"- traffic-counter-api CPU/Memory peak per pod: `{top_samples.get('traffic-counter-api', {}).get('cpu_m', 0)}m / {top_samples.get('traffic-counter-api', {}).get('memory_mi', 0.0):.1f}Mi`",
        f"- meme-content-api CPU/Memory peak per pod: `{top_samples.get('meme-content-api', {}).get('cpu_m', 0)}m / {top_samples.get('meme-content-api', {}).get('memory_mi', 0.0):.1f}Mi`",
        "",
        "## Service Health",
        f"- traffic-app readiness after test: `{ready_status}`",
        f"- counter service status: `{counter_ready}`",
        f"- meme service status: `{meme_ready}`",
        f"- Counter hits before/after: `{initial_hits} -> {final_hits}`",
        f"- Expected counter hit delta: `{expected_hit_delta}`",
        "",
        "## Findings",
        *findings,
        "",
        "## Artifacts",
        f"- Raw load summary: `{(run_dir / 'python-loadtest-summary.json').name}`",
        f"- HPA samples: `{(run_dir / 'hpa-samples.tsv').name}`",
        f"- Pod samples: `{(run_dir / 'pod-samples.tsv').name}`",
        f"- Resource samples: `{(run_dir / 'top-samples.tsv').name}`",
        f"- traffic-app logs: `{(run_dir / 'traffic-app.logs').name}`",
        f"- traffic-counter-api logs: `{(run_dir / 'traffic-counter-api.logs').name}`",
        f"- meme-content-api logs: `{(run_dir / 'meme-content-api.logs').name}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir)
    report = render_report(run_dir)
    report_path = run_dir / "summary.md"
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nReport written to: {report_path}")


if __name__ == "__main__":
    main()

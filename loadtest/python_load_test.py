from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class ResultStore:
    total_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    status_codes: dict[int, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def record_success(self, status_code: int, latency_ms: float) -> None:
        async with self.lock:
            self.total_requests += 1
            self.success_count += 1
            self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1
            self.latencies_ms.append(latency_ms)

    async def record_failure(
        self,
        status_code: int | None,
        latency_ms: float | None,
        error: str,
    ) -> None:
        async with self.lock:
            self.total_requests += 1
            self.failure_count += 1
            if status_code is not None:
                self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1
            if latency_ms is not None:
                self.latencies_ms.append(latency_ms)
            if len(self.errors) < 20:
                self.errors.append(error)


async def worker(
    client: httpx.AsyncClient,
    url: str,
    end_time: float,
    results: ResultStore,
    think_time: float,
) -> None:
    while time.perf_counter() < end_time:
        start = time.perf_counter()
        try:
            response = await client.get(url)
            latency_ms = (time.perf_counter() - start) * 1000
            if response.status_code == 200:
                await results.record_success(response.status_code, latency_ms)
            else:
                await results.record_failure(
                    response.status_code,
                    latency_ms,
                    f"HTTP {response.status_code}",
                )
        except httpx.HTTPError as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            await results.record_failure(None, latency_ms, str(exc))

        if think_time > 0:
            await asyncio.sleep(think_time)


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(ratio * (len(ordered) - 1))))
    return ordered[index]


async def run_load_test(
    url: str,
    concurrency: int,
    duration_seconds: int,
    timeout_seconds: float,
    think_time: float,
) -> ResultStore:
    results = ResultStore()
    end_time = time.perf_counter() + duration_seconds

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=3.0)) as client:
        tasks = [
            asyncio.create_task(worker(client, url, end_time, results, think_time))
            for _ in range(concurrency)
        ]
        await asyncio.gather(*tasks)

    return results


def build_summary(
    results: ResultStore,
    duration_seconds: int,
    *,
    url: str,
    concurrency: int,
    timeout_seconds: float,
    think_time: float,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "config": {
            "url": url,
            "concurrency": concurrency,
            "duration_seconds": duration_seconds,
            "timeout_seconds": timeout_seconds,
            "think_time_seconds": think_time,
        },
        "totals": {
            "total_requests": results.total_requests,
            "success_count": results.success_count,
            "failure_count": results.failure_count,
            "success_rate": (
                (results.success_count / results.total_requests) * 100
                if results.total_requests
                else 0.0
            ),
            "rps": results.total_requests / duration_seconds if duration_seconds else 0.0,
        },
        "status_codes": dict(sorted(results.status_codes.items())),
        "errors": results.errors,
    }

    if results.latencies_ms:
        summary["latency_ms"] = {
            "avg": statistics.mean(results.latencies_ms),
            "p50": percentile(results.latencies_ms, 0.50),
            "p95": percentile(results.latencies_ms, 0.95),
            "max": max(results.latencies_ms),
        }
    else:
        summary["latency_ms"] = {
            "avg": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "max": 0.0,
        }

    return summary


def print_summary(summary: dict[str, Any]) -> None:
    totals = summary["totals"]
    latency = summary["latency_ms"]

    print("\nLoad Test Summary")
    print(f"Total requests: {totals['total_requests']}")
    print(f"Success: {totals['success_count']}")
    print(f"Failure: {totals['failure_count']}")
    print(f"Success rate: {totals['success_rate']:.2f}%")
    print(f"RPS: {totals['rps']:.2f}")
    print(f"Status codes: {summary['status_codes']}")
    print(f"Latency avg: {latency['avg']:.2f} ms")
    print(f"Latency p50: {latency['p50']:.2f} ms")
    print(f"Latency p95: {latency['p95']:.2f} ms")
    print(f"Latency max: {latency['max']:.2f} ms")

    if summary["errors"]:
        print("Sample errors:")
        for error in summary["errors"]:
            print(f"- {error}")


def write_json_summary(path: str, summary: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple async load test for traffic-app.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:57631/api/notice",
        help="Target URL to load test.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=50,
        help="Number of concurrent workers.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Test duration in seconds.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--think-time",
        type=float,
        default=0.0,
        help="Delay between each request per worker in seconds.",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path to write a JSON summary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = asyncio.run(
        run_load_test(
            url=args.url,
            concurrency=args.concurrency,
            duration_seconds=args.duration,
            timeout_seconds=args.timeout,
            think_time=args.think_time,
        )
    )
    summary = build_summary(
        results,
        args.duration,
        url=args.url,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
        think_time=args.think_time,
    )
    print_summary(summary)
    if args.json_out:
        write_json_summary(args.json_out, summary)


if __name__ == "__main__":
    main()

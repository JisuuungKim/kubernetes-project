from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or default


def get_counter_api_url() -> str:
    return get_env("COUNTER_API_URL", "http://localhost:9000") or "http://localhost:9000"


def get_meme_api_url() -> str:
    return get_env("MEME_API_URL", "http://localhost:9100") or "http://localhost:9100"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
    app.state.started_at = utc_now()
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="SKALA Slack Notice Gateway",
    description="Ingress-facing app that records traffic and fetches notice content from internal services.",
    version="4.0.0",
    lifespan=lifespan,
)


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


async def call_counter_hit(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        response = await client.get(f"{get_counter_api_url()}/hit")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Traffic Counter API request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Traffic Counter API returned HTTP {response.status_code}",
        )

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Traffic Counter API returned invalid JSON",
        ) from exc


async def call_counter_stats(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        response = await client.get(f"{get_counter_api_url()}/stats")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Traffic Counter API stats request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Traffic Counter API stats returned HTTP {response.status_code}",
        )

    return response.json()


async def call_meme_content(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        response = await client.get(f"{get_meme_api_url()}/meme")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Meme Content API request failed: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Meme Content API returned HTTP {response.status_code}",
        )

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Meme Content API returned invalid JSON",
        ) from exc


async def call_service_health(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    try:
        response = await client.get(f"{base_url}/health")
    except httpx.HTTPError as exc:
        return {"status": "error", "detail": str(exc)}

    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = {"status": "invalid_json"}

    payload["http_status"] = response.status_code
    return payload


def build_notice_response(
    *,
    message: str,
    counter_result: dict[str, Any] | None = None,
    meme_result: dict[str, Any] | None = None,
    flow: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "service": "traffic-app",
        "flow": flow,
        "message": message,
        "requested_at": isoformat(utc_now()),
    }
    if counter_result is not None:
        payload["counter"] = counter_result
    if meme_result is not None:
        payload["notice"] = meme_result.get("meme")
        payload["meme_source"] = meme_result
    return payload


@app.get("/notice")
async def get_notice(request: Request) -> dict[str, Any]:
    client = get_http_client(request)
    counter_result = await call_counter_hit(client)
    meme_result = await call_meme_content(client)

    return build_notice_response(
        message="notice delivered successfully",
        counter_result=counter_result,
        meme_result=meme_result,
        flow="counter_then_meme",
    )


@app.get("/notice/message")
async def get_notice_message(request: Request) -> dict[str, Any]:
    client = get_http_client(request)
    meme_result = await call_meme_content(client)

    return build_notice_response(
        message="meme notice fetched successfully",
        meme_result=meme_result,
        flow="meme_only",
    )


@app.get("/notice/track")
async def track_notice_request(request: Request) -> dict[str, Any]:
    client = get_http_client(request)
    counter_result = await call_counter_hit(client)

    return build_notice_response(
        message="notice traffic recorded successfully",
        counter_result=counter_result,
        flow="counter_only",
    )


@app.get("/stats")
async def get_stats(request: Request) -> dict[str, Any]:
    client = get_http_client(request)
    started_at: datetime = request.app.state.started_at
    counter_stats = await call_counter_stats(client)

    return {
        "service": "traffic-app",
        "process_started_at": isoformat(started_at),
        "uptime_seconds": int((utc_now() - started_at).total_seconds()),
        "counter_api_url": get_counter_api_url(),
        "meme_api_url": get_meme_api_url(),
        "counter_stats": counter_stats,
    }


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive", "service": "traffic-app"}


@app.get("/health/ready")
async def readiness(request: Request) -> dict[str, Any]:
    client = get_http_client(request)
    counter_health, meme_health = await asyncio.gather(
        call_service_health(client, get_counter_api_url()),
        call_service_health(client, get_meme_api_url()),
    )
    return {
        "status": "ready",
        "service": "traffic-app",
        "counter_service": counter_health,
        "meme_service": meme_health,
    }


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return await readiness(request)

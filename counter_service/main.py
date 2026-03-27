from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError


HIT_COUNT_KEY = "traffic-counter:hit_count"
FIRST_HIT_KEY = "traffic-counter:first_hit_at"
LAST_HIT_KEY = "traffic-counter:last_hit_at"


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


def get_redis_url() -> str:
    explicit_url = get_env("REDIS_URL")
    if explicit_url:
        return explicit_url

    host = get_env("REDIS_HOST", "localhost")
    port = get_env("REDIS_PORT", "6379")
    db = get_env("REDIS_DB", "0")
    password = get_env("REDIS_PASSWORD")

    if password:
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = Redis.from_url(get_redis_url(), decode_responses=True)
    app.state.started_at = utc_now()
    yield
    await app.state.redis.aclose()


app = FastAPI(
    title="Traffic Counter API",
    description="Redis-backed hit counter for load tracking.",
    version="1.0.0",
    lifespan=lifespan,
)


def get_redis() -> Redis:
    return app.state.redis


async def ensure_redis(redis: Redis) -> None:
    try:
        await redis.ping()
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Redis unavailable: {exc}",
        ) from exc


@app.get("/hit")
@app.post("/hit")
async def hit() -> dict[str, Any]:
    redis = get_redis()
    await ensure_redis(redis)
    now = isoformat(utc_now())

    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(HIT_COUNT_KEY)
        pipe.setnx(FIRST_HIT_KEY, now)
        pipe.set(LAST_HIT_KEY, now)
        results = await pipe.execute()

    return {
        "service": "traffic-counter-api",
        "total_hits": int(results[0]),
        "recorded_at": now,
    }


@app.get("/stats")
async def stats() -> dict[str, Any]:
    redis = get_redis()
    await ensure_redis(redis)

    async with redis.pipeline(transaction=False) as pipe:
        pipe.get(HIT_COUNT_KEY)
        pipe.get(FIRST_HIT_KEY)
        pipe.get(LAST_HIT_KEY)
        total_hits, first_hit_at, last_hit_at = await pipe.execute()

    started_at = app.state.started_at
    return {
        "service": "traffic-counter-api",
        "total_hits": int(total_hits or 0),
        "first_hit_at": first_hit_at,
        "last_hit_at": last_hit_at,
        "started_at": isoformat(started_at),
        "uptime_seconds": int((utc_now() - started_at).total_seconds()),
    }


@app.get("/health")
async def health() -> dict[str, str]:
    redis = get_redis()
    await ensure_redis(redis)
    return {"status": "ok", "service": "traffic-counter-api"}

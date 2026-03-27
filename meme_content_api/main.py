from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI


MEMES = [
    "3반 전원 합격, 오늘은 축제다!",
    "합격 축하합니다. 이제 공지보다 박수가 먼저입니다.",
    "서버도 버티고, 3반도 붙었습니다.",
    "오늘 공지는 알림이 아니라 자랑입니다.",
    "합격률 100%, 트래픽도 1000%!",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


app = FastAPI(
    title="Meme Content API",
    description="Provides short celebratory message content for the notice app.",
    version="1.0.0",
)


@app.get("/meme")
async def get_meme() -> dict[str, Any]:
    return {
        "service": "meme-content-api",
        "meme": random.choice(MEMES),
        "generated_at": isoformat(utc_now()),
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "meme-content-api"}

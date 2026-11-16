"""Small sliding-window rate limiter, usable as a FastAPI dependency.

State is per process (fine for one worker and for tests). With several
workers or hosts, put a shared limiter (Redis, gateway) in front instead -
documented as a known limit.
"""
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

from app.core.config import settings

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def reset_rate_limits() -> None:
    with _lock:
        _hits.clear()


def _client_key(request: Request) -> str:
    # Prefer the authenticated token fingerprint; fall back to the client IP.
    auth = request.headers.get("authorization", "")
    if auth:
        return "t:" + str(hash(auth))
    return "ip:" + (request.client.host if request.client else "unknown")


def rate_limit(limit: int | None = None, window_seconds: int = 60):
    """Dependency factory: ``Depends(rate_limit(30))`` allows 30 calls/minute."""

    def checker(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        allowed = limit or settings.rate_limit_per_minute
        key = f"{request.url.path}|{_client_key(request)}"
        now = time.monotonic()
        with _lock:
            bucket = _hits[key]
            while bucket and now - bucket[0] >= window_seconds:
                bucket.popleft()
            if len(bucket) >= allowed:
                retry = max(1, int(window_seconds - (now - bucket[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests",
                    headers={"Retry-After": str(retry)},
                )
            bucket.append(now)

    return checker

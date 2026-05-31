import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

_RATE_LIMIT_EXCEEDED = "Too many admin requests. Please wait and try again."

# Basic in-memory admin protection. This is intentionally small and local to
# one process; replace it with Redis or another shared store before scaling the
# admin API across multiple workers/instances.
_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = Lock()


def enforce_admin_rate_limit(
    request: Request,
    action: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    now = time.monotonic()
    client_ip = _client_ip(request)
    key = (client_ip, action)
    cutoff = now - window_seconds

    with _lock:
        bucket = _buckets[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            raise HTTPException(status_code=429, detail=_RATE_LIMIT_EXCEEDED)
        bucket.append(now)


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", maxsplit=1)[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

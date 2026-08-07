"""PID 1 supervisor for the internal BgUtils provider and FastAPI."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


children: list[subprocess.Popen[bytes]] = []
stopping = False


def log(event: str, **fields: object) -> None:
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{event}{' ' + suffix if suffix else ''}", flush=True)


def stop_children(*_args: object) -> None:
    global stopping
    if stopping:
        return
    stopping = True
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + max(
        10.0,
        float(os.getenv("SERVICE_SHUTDOWN_TIMEOUT_SECONDS", "35")),
    )
    for child in reversed(children):
        if child.poll() is not None:
            continue
        try:
            child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def wait_for_provider(base_url: str, provider: subprocess.Popen[bytes]) -> bool:
    deadline = time.monotonic() + float(os.getenv("BGUTIL_READY_TIMEOUT_SECONDS", "45"))
    while time.monotonic() < deadline:
        if provider.poll() is not None:
            return False
        try:
            with urlopen(f"{base_url.rstrip('/')}/ping", timeout=2) as response:
                if getattr(response, "status", 200) == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_children)

    bgutil_enabled = os.getenv("BGUTIL_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    if bgutil_enabled:
        server_dir = Path(os.getenv("BGUTIL_SERVER_DIR", "/opt/bgutil/server"))
        base_url = os.getenv("BGUTIL_BASE_URL", "http://127.0.0.1:4416")
        provider_port = urlparse(base_url).port or 4416
        provider = subprocess.Popen(
            [
                "deno", "run", "--allow-env", "--allow-net", "--allow-ffi=.",
                "--allow-read=.", "../src/main.ts", "--port", str(provider_port),
            ],
            cwd=server_dir / "node_modules",
        )
        children.append(provider)
        if not wait_for_provider(base_url, provider):
            log("pot_provider_failed")
            stop_children()
            return 1
        log("pot_provider_ready", baseUrl=base_url)

    uvicorn = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    )
    children.append(uvicorn)
    while not stopping:
        for child in children:
            return_code = child.poll()
            if return_code is not None:
                log("supervised_process_exited", returnCode=return_code)
                stop_children()
                return return_code
        time.sleep(0.5)
    stop_children()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

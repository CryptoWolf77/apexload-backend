from __future__ import annotations

import ipaddress
import json
import logging
import random
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.core.config import Settings, get_settings
from app.services.youtube_error_classifier import classify_youtube_error

logger = logging.getLogger("apexload.youtube_proxy")


class YouTubeProxyUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class ProxyHealth:
    proxy_url: str
    last_success: float | None = None
    last_failure: float | None = None
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    average_validation_latency: float | None = None
    cooldown_until: float = 0.0
    last_youtube_status: str = "unknown"
    last_exit_ip: str | None = None

    def score(self, now: float) -> float:
        recency = 0.0 if self.last_success is None else max(0.0, 600.0 - (now - self.last_success)) / 60.0
        reliability = (self.success_count + 1) / (self.success_count + self.failure_count + 2)
        latency_penalty = self.average_validation_latency or 5.0
        return recency + (reliability * 10.0) - latency_penalty - (self.consecutive_failures * 4.0)

    def is_healthy(self, now: float, ttl: int) -> bool:
        return (
            self.last_success is not None
            and now - self.last_success <= ttl
            and now >= self.cooldown_until
        )


def normalize_proxy_entry(value: str) -> str | None:
    candidate = value.strip().split()[0] if value.strip() else ""
    if not candidate or candidate.startswith("#"):
        return None
    if "://" not in candidate:
        candidate = f"socks5://{candidate}"
    try:
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in {"socks5", "socks5h"} or parsed.username or parsed.password:
            return None
        if not parsed.hostname or parsed.port is None or not 1 <= parsed.port <= 65535:
            return None
        address = ipaddress.ip_address(parsed.hostname)
    except (ValueError, ipaddress.AddressValueError):
        return None
    if address.version != 4 or not address.is_global:
        return None
    return f"socks5://{address.compressed}:{parsed.port}"


def parse_proxy_list(text: str) -> list[str]:
    proxies: dict[str, None] = {}
    for line in text.splitlines():
        normalized = normalize_proxy_entry(line)
        if normalized:
            proxies.setdefault(normalized, None)
    return list(proxies)


class YouTubeProxyManager:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        clock: Callable[[], float] = time.time,
        validator: Callable[[str, str], tuple[bool, float, str]] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._clock = clock
        self._validator = validator or self._validate_youtube
        self._records: dict[str, ProxyHealth] = {}
        self._condition = threading.Condition(threading.RLock())
        self._discovery_running = False
        self._source_cache: tuple[float, list[str]] | None = None
        self._selection_counter = 0
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._background_thread: threading.Thread | None = None
        self._maintenance_url = self.settings.youtube_proxy_prewarm_url

    def start_background(self) -> None:
        if (
            not self.settings.youtube_proxy_enabled
            or not self.settings.youtube_proxy_prewarm_enabled
            or not _is_youtube_url(self.settings.youtube_proxy_prewarm_url)
        ):
            return
        with self._condition:
            if self._background_thread and self._background_thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._maintenance_url = self.settings.youtube_proxy_prewarm_url
            self._background_thread = threading.Thread(
                target=self._background_loop,
                name="youtube-proxy-pool",
                daemon=True,
            )
            self._background_thread.start()
        logger.info("proxy_pool_background_started")

    def stop_background(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._condition:
            thread = self._background_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self.settings.youtube_proxy_shutdown_timeout_seconds)
        with self._condition:
            if thread and thread.is_alive():
                logger.warning("proxy_pool_background_stop_timed_out")
            elif self._background_thread is thread:
                self._background_thread = None
        logger.info("proxy_pool_background_stopped")

    def wait_until_ready(self, timeout: float) -> bool:
        if timeout <= 0:
            return False
        with self._condition:
            awakened = self._condition.wait_for(
                lambda: (
                    self._healthy_count_locked() >= self.settings.youtube_proxy_pool_target
                    or self._stop_event.is_set()
                ),
                timeout=timeout,
            )
            ready = awakened and self._healthy_count_locked() >= self.settings.youtube_proxy_pool_target
        logger.info(
            "proxy_pool_startup_ready" if ready else "proxy_pool_startup_wait_expired"
        )
        return ready

    def request_maintenance(self, url: str | None = None) -> None:
        with self._condition:
            if url and _is_youtube_url(url):
                self._maintenance_url = url
        self._wake_event.set()

    def acquire(self, url: str, excluded: set[str] | None = None) -> str:
        if not _is_youtube_url(url):
            raise YouTubeProxyUnavailableError("Proxy manager accepts only YouTube URLs")
        excluded = excluded or set()
        with self._condition:
            selected = self._choose_healthy(excluded)
            if selected:
                selected_url = selected.proxy_url
                needs_replenishment = self._maintenance_healthy_count_locked() < self.settings.youtube_proxy_pool_target
                if needs_replenishment:
                    self._maintenance_url = url
                    self._wake_event.set()
                return selected_url
            if self._discovery_running:
                wait_seconds = max(10.0, self.settings.youtube_proxy_validation_timeout * 2)
                self._condition.wait_for(
                    lambda: (
                        not self._discovery_running
                        or self._has_healthy_locked(excluded)
                    ),
                    timeout=wait_seconds,
                )
                selected = self._choose_healthy(excluded)
                if selected:
                    return selected.proxy_url
                if self._discovery_running:
                    raise YouTubeProxyUnavailableError("Timed out waiting for proxy discovery")
            self._discovery_running = True

        try:
            self._discover(url, excluded)
        finally:
            with self._condition:
                self._discovery_running = False
                self._condition.notify_all()

        with self._condition:
            selected = self._choose_healthy(excluded)
            if not selected:
                logger.warning("youtube_proxy_exhausted")
                raise YouTubeProxyUnavailableError("No validated YouTube proxy is available")
            return selected.proxy_url

    def report_success(self, proxy_url: str, latency: float | None = None) -> None:
        now = self._clock()
        with self._condition:
            record = self._records.setdefault(proxy_url, ProxyHealth(proxy_url))
            record.last_success = now
            record.success_count += 1
            record.consecutive_failures = 0
            record.cooldown_until = 0.0
            record.last_youtube_status = "success"
            if latency is not None:
                record.average_validation_latency = (
                    latency if record.average_validation_latency is None
                    else (record.average_validation_latency * 0.7) + (latency * 0.3)
                )
            needs_replenishment = self._maintenance_healthy_count_locked() < self.settings.youtube_proxy_pool_target
            self._condition.notify_all()
        if needs_replenishment:
            self._wake_event.set()

    def report_failure(self, proxy_url: str, error: BaseException | str) -> None:
        now = self._clock()
        classification = classify_youtube_error(error)
        with self._condition:
            record = self._records.setdefault(proxy_url, ProxyHealth(proxy_url))
            record.last_failure = now
            record.failure_count += 1
            record.consecutive_failures += 1
            record.last_youtube_status = classification.code.value
            if record.consecutive_failures >= self.settings.youtube_proxy_max_consecutive_failures:
                record.cooldown_until = now + self.settings.youtube_proxy_failure_cooldown_seconds
            self._condition.notify_all()
        self._wake_event.set()

    def snapshot(self) -> list[ProxyHealth]:
        with self._condition:
            return [replace(record) for record in self._records.values()]

    def _choose_healthy(self, excluded: set[str]) -> ProxyHealth | None:
        now = self._clock()
        candidates = [
            record for record in self._records.values()
            if record.proxy_url not in excluded
            and record.is_healthy(now, self.settings.youtube_proxy_cache_ttl_seconds)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.score(now), reverse=True)
        top = candidates[: min(3, len(candidates))]
        selected = top[self._selection_counter % len(top)]
        self._selection_counter += 1
        logger.info("youtube_proxy_selected proxyId=%s", self._proxy_id(selected.proxy_url))
        return selected

    def _healthy_count_locked(self) -> int:
        now = self._clock()
        return sum(
            record.is_healthy(now, self.settings.youtube_proxy_cache_ttl_seconds)
            for record in self._records.values()
        )

    def _has_healthy_locked(self, excluded: set[str]) -> bool:
        now = self._clock()
        return any(
            record.proxy_url not in excluded
            and record.is_healthy(now, self.settings.youtube_proxy_cache_ttl_seconds)
            for record in self._records.values()
        )

    def _maintenance_healthy_count_locked(self) -> int:
        maintenance_ttl = max(
            1,
            self.settings.youtube_proxy_cache_ttl_seconds
            - self.settings.youtube_proxy_revalidate_ahead_seconds,
        )
        now = self._clock()
        return sum(
            record.is_healthy(now, maintenance_ttl)
            for record in self._records.values()
        )

    def _background_loop(self) -> None:
        backoff = self.settings.youtube_proxy_refresh_backoff_seconds
        while not self._stop_event.is_set():
            with self._condition:
                healthy_count = self._maintenance_healthy_count_locked()
                url = self._maintenance_url or self.settings.youtube_proxy_prewarm_url
            if healthy_count < self.settings.youtube_proxy_pool_target and url:
                self._warm_pool(url)
                with self._condition:
                    healthy_count = self._maintenance_healthy_count_locked()
                if healthy_count < self.settings.youtube_proxy_pool_target:
                    wait_seconds = backoff
                    backoff = min(
                        backoff * 2,
                        self.settings.youtube_proxy_refresh_max_backoff_seconds,
                    )
                else:
                    backoff = self.settings.youtube_proxy_refresh_backoff_seconds
                    wait_seconds = self.settings.youtube_proxy_maintenance_interval_seconds
            else:
                backoff = self.settings.youtube_proxy_refresh_backoff_seconds
                wait_seconds = self.settings.youtube_proxy_maintenance_interval_seconds
            self._wake_event.wait(timeout=wait_seconds)
            self._wake_event.clear()

    def _warm_pool(self, url: str) -> int:
        with self._condition:
            maintenance_ttl = max(
                1,
                self.settings.youtube_proxy_cache_ttl_seconds
                - self.settings.youtube_proxy_revalidate_ahead_seconds,
            )
            healthy = {
                record.proxy_url
                for record in self._records.values()
                if record.is_healthy(
                    self._clock(),
                    maintenance_ttl,
                )
            }
            deficit = self.settings.youtube_proxy_pool_target - len(healthy)
            if deficit <= 0 or self._discovery_running or self._stop_event.is_set():
                return 0
            self._discovery_running = True
        try:
            return self._discover(url, healthy, target_count=deficit)
        finally:
            with self._condition:
                self._discovery_running = False
                self._condition.notify_all()

    def _discover(
        self,
        url: str,
        excluded: set[str],
        *,
        target_count: int | None = None,
    ) -> int:
        started = time.monotonic()
        logger.info("proxy_pool_refresh_started")
        target_count = target_count or self.settings.youtube_proxy_pool_target
        candidates = [proxy for proxy in self._load_candidates() if proxy not in excluded]
        random.shuffle(candidates)
        candidates = candidates[: self.settings.youtube_proxy_max_candidates]
        connectivity: list[tuple[str, str | None]] = []
        desired = max(target_count * 3, target_count)
        with ThreadPoolExecutor(max_workers=self.settings.youtube_proxy_health_concurrency) as executor:
            futures = {executor.submit(self._connectivity_check, proxy): proxy for proxy in candidates}
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                proxy = futures[future]
                try:
                    passed, exit_ip = future.result()
                except Exception:
                    passed, exit_ip = False, None
                if passed:
                    connectivity.append((proxy, exit_ip))
                    logger.info("proxy_connectivity_passed proxyId=%s", self._proxy_id(proxy))
                    if len(connectivity) >= desired:
                        for pending in futures:
                            pending.cancel()
                        break

        successes = 0
        with ThreadPoolExecutor(max_workers=self.settings.youtube_proxy_validation_concurrency) as executor:
            futures = {
                executor.submit(self._validator, proxy, url): (proxy, exit_ip)
                for proxy, exit_ip in connectivity
            }
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                proxy, exit_ip = futures[future]
                try:
                    valid, latency, status = future.result()
                except Exception as exc:
                    valid, latency, status = False, 0.0, str(exc)
                if valid:
                    self.report_success(proxy, latency)
                    with self._condition:
                        self._records[proxy].last_exit_ip = exit_ip
                    successes += 1
                    logger.info("proxy_youtube_validation_passed proxyId=%s latencyMs=%s", self._proxy_id(proxy), round(latency * 1000))
                    if successes >= target_count:
                        for pending in futures:
                            pending.cancel()
                        break
                else:
                    self.report_failure(proxy, status)
                    logger.info("proxy_youtube_validation_failed proxyId=%s status=%s", self._proxy_id(proxy), classify_youtube_error(status).code.value)
        logger.info("proxy_pool_refresh_completed candidates=%s connectivity=%s healthy=%s elapsedMs=%s", len(candidates), len(connectivity), successes, round((time.monotonic() - started) * 1000))
        return successes

    def _load_candidates(self) -> list[str]:
        now = self._clock()
        with self._condition:
            if self._source_cache and now - self._source_cache[0] <= self.settings.youtube_proxy_source_cache_ttl_seconds:
                return list(self._source_cache[1])
        collected: dict[str, None] = {}
        for source in self.settings.youtube_proxy_source_urls:
            if not self._source_allowed(source):
                logger.warning("Rejected non-allow-listed proxy source host")
                continue
            try:
                request = Request(source, headers={"User-Agent": "ApexLoad-ProxyManager/1.0"})
                with urlopen(request, timeout=10) as response:
                    payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise ValueError("proxy source response too large")
                for proxy in parse_proxy_list(payload.decode("utf-8", errors="ignore")):
                    collected.setdefault(proxy, None)
            except Exception as exc:
                logger.warning("Proxy source fetch failed host=%s error=%s", urlparse(source).hostname, type(exc).__name__)
        result = list(collected)
        with self._condition:
            self._source_cache = (now, result)
        return result

    def _source_allowed(self, source: str) -> bool:
        parsed = urlparse(source)
        return parsed.scheme == "https" and (parsed.hostname or "").lower() in set(self.settings.youtube_proxy_source_allowed_hosts)

    def _connectivity_check(self, proxy_url: str) -> tuple[bool, str | None]:
        target = urlparse(self.settings.youtube_proxy_connectivity_url)
        if target.scheme != "http" or (target.hostname or "").lower() != "api.iplocate.io":
            return False, None
        proxy = urlparse(proxy_url)
        timeout = self.settings.youtube_proxy_health_timeout
        with socket.create_connection((proxy.hostname, proxy.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            if _recv_exact(sock, 2) != b"\x05\x00":
                return False, None
            host_bytes = target.hostname.encode("idna")
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack("!H", target.port or 80))
            reply = _recv_exact(sock, 4)
            if reply[1] != 0:
                return False, None
            address_length = {1: 4, 4: 16}.get(reply[3])
            if reply[3] == 3:
                address_length = _recv_exact(sock, 1)[0]
            if address_length is None:
                return False, None
            _recv_exact(sock, address_length + 2)
            path = target.path or "/"
            if target.query:
                path += f"?{target.query}"
            sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {target.hostname}\r\nConnection: close\r\nAccept: application/json\r\n\r\n".encode())
            data = bytearray()
            while len(data) < 16_384:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
        header, _, body = bytes(data).partition(b"\r\n\r\n")
        if b" 200 " not in header.split(b"\r\n", 1)[0]:
            return False, None
        try:
            payload = json.loads(body.decode("utf-8", errors="ignore"))
            exit_ip = payload.get("ip") if isinstance(payload, dict) else None
        except ValueError:
            exit_ip = None
        return True, exit_ip if isinstance(exit_ip, str) else None

    def _validate_youtube(self, proxy_url: str, url: str) -> tuple[bool, float, str]:
        started = time.monotonic()
        try:
            import yt_dlp
            from app.services.ytdlp_options import apply_anonymous_youtube_proxy, build_ytdlp_options

            options = build_ytdlp_options("YouTube Shorts", "validate", {
                "socket_timeout": self.settings.youtube_proxy_validation_timeout,
                "retries": 0,
                "extractor_retries": 0,
            }, anonymous_youtube=True)
            apply_anonymous_youtube_proxy(options, proxy_url)
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            formats = info.get("formats") if isinstance(info, dict) else None
            usable = isinstance(formats, list) and any(
                isinstance(item, dict) and item.get("url") and (item.get("vcodec") != "none" or item.get("acodec") != "none")
                for item in formats
            )
            return usable, time.monotonic() - started, "success" if usable else "no usable media formats"
        except Exception as exc:
            return False, time.monotonic() - started, str(exc)

    @staticmethod
    def _proxy_id(proxy_url: str) -> str:
        # Logs retain correlation without publishing the complete route.
        return str(abs(hash(proxy_url)) % 1_000_000).zfill(6)


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("SOCKS proxy closed the connection")
        data.extend(chunk)
    return bytes(data)


def _is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and (
            host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "youtu.be"
            or host.endswith(".youtu.be")
        )
    )


youtube_proxy_manager = YouTubeProxyManager()

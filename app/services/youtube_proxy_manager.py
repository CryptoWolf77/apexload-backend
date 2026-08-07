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
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from app.core.config import Settings, get_settings
from app.services.youtube_error_classifier import classify_youtube_error

logger = logging.getLogger("apexload.youtube_proxy")
MAX_CAPABILITY_CACHE_ENTRIES = 2_048


class YouTubeProxyUnavailableError(RuntimeError):
    pass


class YouTubeProxyCapabilityUnavailableError(YouTubeProxyUnavailableError):
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


@dataclass(frozen=True, slots=True)
class ProxyCapability:
    resource_key: str
    proxy_url: str
    available_resolutions: frozenset[int]
    validated_at: float
    latency: float | None = None


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
        capability_validator: Callable[
            [str, str], tuple[set[int], float, str]
        ] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._clock = clock
        self._validator = validator or self._validate_youtube
        self._capability_validator = (
            capability_validator or self._validate_youtube_capability
        )
        self._records: dict[str, ProxyHealth] = {}
        self._capabilities: dict[tuple[str, str], ProxyCapability] = {}
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

    def acquire(
        self,
        url: str,
        excluded: set[str] | None = None,
        *,
        required_resolution: int | None = None,
    ) -> str:
        if not _is_youtube_url(url):
            raise YouTubeProxyUnavailableError("Proxy manager accepts only YouTube URLs")
        if required_resolution is not None and required_resolution <= 0:
            raise ValueError("required_resolution must be positive")
        excluded = excluded or set()
        with self._condition:
            selected = (
                self._choose_capable(url, required_resolution, excluded)
                if required_resolution is not None
                else self._choose_healthy(excluded)
            )
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
                        or (
                            self._has_capable_locked(
                                url,
                                required_resolution,
                                excluded,
                            )
                            if required_resolution is not None
                            else self._has_healthy_locked(excluded)
                        )
                    ),
                    timeout=wait_seconds,
                )
                selected = (
                    self._choose_capable(url, required_resolution, excluded)
                    if required_resolution is not None
                    else self._choose_healthy(excluded)
                )
                if selected:
                    return selected.proxy_url
                if self._discovery_running:
                    raise YouTubeProxyUnavailableError("Timed out waiting for proxy discovery")
            self._discovery_running = True

        try:
            if required_resolution is not None:
                self._discover_capability(url, required_resolution, excluded)
            else:
                self._discover(url, excluded)
        finally:
            with self._condition:
                self._discovery_running = False
                self._condition.notify_all()

        with self._condition:
            selected = (
                self._choose_capable(url, required_resolution, excluded)
                if required_resolution is not None
                else self._choose_healthy(excluded)
            )
            if not selected:
                logger.warning("youtube_proxy_exhausted")
                if required_resolution is not None:
                    raise YouTubeProxyCapabilityUnavailableError(
                        "No validated YouTube proxy exposes the requested resolution"
                    )
                raise YouTubeProxyUnavailableError("No validated YouTube proxy is available")
            return selected.proxy_url

    def record_capability(
        self,
        url: str,
        proxy_url: str,
        available_resolutions: set[int],
        latency: float | None = None,
    ) -> None:
        now = self._clock()
        resource_key = _youtube_resource_key(url)
        resolutions = frozenset(
            value
            for value in available_resolutions
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        )
        capability = ProxyCapability(
            resource_key=resource_key,
            proxy_url=proxy_url,
            available_resolutions=resolutions,
            validated_at=now,
            latency=latency,
        )
        with self._condition:
            self._prune_capabilities_locked(now)
            capability_key = (resource_key, proxy_url)
            if (
                capability_key not in self._capabilities
                and len(self._capabilities) >= MAX_CAPABILITY_CACHE_ENTRIES
            ):
                oldest_key = min(
                    self._capabilities,
                    key=lambda key: self._capabilities[key].validated_at,
                )
                self._capabilities.pop(oldest_key, None)
            self._capabilities[capability_key] = capability
            self._condition.notify_all()
        logger.info(
            "youtube_proxy_capability_recorded proxyId=%s resolutions=%s",
            self._proxy_id(proxy_url),
            sorted(resolutions),
        )

    def cached_resolutions(self, url: str, proxy_url: str) -> set[int] | None:
        now = self._clock()
        with self._condition:
            self._prune_capabilities_locked(now)
            capability = self._capabilities.get(
                (_youtube_resource_key(url), proxy_url)
            )
            return set(capability.available_resolutions) if capability else None

    def forget_capability(self, url: str, proxy_url: str) -> None:
        with self._condition:
            self._capabilities.pop((_youtube_resource_key(url), proxy_url), None)
            self._condition.notify_all()

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

    def _choose_capable(
        self,
        url: str,
        required_resolution: int,
        excluded: set[str],
    ) -> ProxyHealth | None:
        now = self._clock()
        self._prune_capabilities_locked(now)
        resource_key = _youtube_resource_key(url)
        candidates = [
            record
            for record in self._records.values()
            if record.proxy_url not in excluded
            and record.is_healthy(now, self.settings.youtube_proxy_cache_ttl_seconds)
            and (
                capability := self._capabilities.get(
                    (resource_key, record.proxy_url)
                )
            ) is not None
            and required_resolution in capability.available_resolutions
        ]
        if not candidates:
            logger.info(
                "youtube_proxy_capability_miss requiredResolution=%s",
                required_resolution,
            )
            return None
        candidates.sort(key=lambda item: item.score(now), reverse=True)
        selected = candidates[0]
        logger.info(
            "youtube_proxy_capability_hit proxyId=%s requiredResolution=%s",
            self._proxy_id(selected.proxy_url),
            required_resolution,
        )
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

    def _has_capable_locked(
        self,
        url: str,
        required_resolution: int,
        excluded: set[str],
    ) -> bool:
        now = self._clock()
        self._prune_capabilities_locked(now)
        resource_key = _youtube_resource_key(url)
        return any(
            record.proxy_url not in excluded
            and record.is_healthy(now, self.settings.youtube_proxy_cache_ttl_seconds)
            and (
                capability := self._capabilities.get(
                    (resource_key, record.proxy_url)
                )
            ) is not None
            and required_resolution in capability.available_resolutions
            for record in self._records.values()
        )

    def _prune_capabilities_locked(self, now: float) -> None:
        ttl = self.settings.youtube_proxy_cache_ttl_seconds
        expired = [
            key
            for key, capability in self._capabilities.items()
            if now - capability.validated_at > ttl
        ]
        for key in expired:
            self._capabilities.pop(key, None)

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

    def _discover_capability(
        self,
        url: str,
        required_resolution: int,
        excluded: set[str],
    ) -> bool:
        started = time.monotonic()
        logger.info(
            "youtube_proxy_capability_discovery_started requiredResolution=%s",
            required_resolution,
        )
        resource_key = _youtube_resource_key(url)
        with self._condition:
            now = self._clock()
            self._prune_capabilities_locked(now)
            healthy_candidates = [
                record
                for record in self._records.values()
                if record.proxy_url not in excluded
                and record.is_healthy(
                    now,
                    self.settings.youtube_proxy_cache_ttl_seconds,
                )
                and (resource_key, record.proxy_url) not in self._capabilities
            ]
        healthy_candidates.sort(key=lambda item: item.score(now), reverse=True)
        checked = {record.proxy_url for record in healthy_candidates}
        if self._validate_capability_candidates(
            url,
            required_resolution,
            [(record.proxy_url, record.last_exit_ip) for record in healthy_candidates],
        ):
            return True

        with self._condition:
            known_misses = {
                capability.proxy_url
                for capability_key, capability in self._capabilities.items()
                if capability_key[0] == resource_key
                and required_resolution not in capability.available_resolutions
            }
        candidates = [
            proxy
            for proxy in self._load_candidates()
            if proxy not in excluded
            and proxy not in checked
            and proxy not in known_misses
        ]
        random.shuffle(candidates)
        candidates = candidates[: self.settings.youtube_proxy_max_candidates]
        desired = min(
            len(candidates),
            max(
                self.settings.youtube_proxy_validation_concurrency,
                self.settings.youtube_proxy_max_job_attempts * 3,
            ),
        )
        connectivity = self._targeted_connectivity_candidates(candidates, desired)
        found = self._validate_capability_candidates(
            url,
            required_resolution,
            connectivity,
        )
        logger.info(
            "youtube_proxy_capability_discovery_completed requiredResolution=%s "
            "healthyCandidates=%s freshCandidates=%s connected=%s found=%s elapsedMs=%s",
            required_resolution,
            len(healthy_candidates),
            len(candidates),
            len(connectivity),
            found,
            round((time.monotonic() - started) * 1000),
        )
        return found

    def _targeted_connectivity_candidates(
        self,
        candidates: list[str],
        desired: int,
    ) -> list[tuple[str, str | None]]:
        if desired <= 0:
            return []
        connectivity: list[tuple[str, str | None]] = []
        with ThreadPoolExecutor(
            max_workers=self.settings.youtube_proxy_health_concurrency
        ) as executor:
            futures = {
                executor.submit(self._connectivity_check, proxy): proxy
                for proxy in candidates
            }
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
                    if len(connectivity) >= desired:
                        for pending in futures:
                            pending.cancel()
                        break
        return connectivity

    def _validate_capability_candidates(
        self,
        url: str,
        required_resolution: int,
        candidates: list[tuple[str, str | None]],
    ) -> bool:
        if not candidates:
            return False
        found = False
        with ThreadPoolExecutor(
            max_workers=self.settings.youtube_proxy_validation_concurrency
        ) as executor:
            futures = {
                executor.submit(self._capability_validator, proxy, url): (
                    proxy,
                    exit_ip,
                )
                for proxy, exit_ip in candidates
            }
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                proxy, exit_ip = futures[future]
                try:
                    resolutions, latency, status = future.result()
                except Exception as exc:
                    resolutions, latency, status = set(), 0.0, str(exc)
                self.record_capability(url, proxy, resolutions, latency)
                if resolutions:
                    self.report_success(proxy, latency)
                    with self._condition:
                        self._records[proxy].last_exit_ip = exit_ip
                else:
                    self.report_failure(proxy, status)
                if required_resolution in resolutions:
                    logger.info(
                        "youtube_proxy_resolution_validation_passed "
                        "proxyId=%s requiredResolution=%s",
                        self._proxy_id(proxy),
                        required_resolution,
                    )
                    found = True
                    for pending in futures:
                        pending.cancel()
                    break
                logger.info(
                    "youtube_proxy_resolution_validation_failed "
                    "proxyId=%s requiredResolution=%s availableResolutions=%s",
                    self._proxy_id(proxy),
                    required_resolution,
                    sorted(resolutions),
                )
        return found

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

    def _validate_youtube_capability(
        self,
        proxy_url: str,
        url: str,
    ) -> tuple[set[int], float, str]:
        started = time.monotonic()
        try:
            import yt_dlp
            from app.services.ytdlp_options import (
                apply_anonymous_youtube_proxy,
                build_ytdlp_options,
            )

            options = build_ytdlp_options(
                "YouTube Shorts",
                "validate",
                {
                    "socket_timeout": self.settings.youtube_proxy_validation_timeout,
                    "retries": 0,
                    "extractor_retries": 0,
                },
                anonymous_youtube=True,
            )
            apply_anonymous_youtube_proxy(options, proxy_url)
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            resolutions = youtube_video_resolutions(info)
            status = "success" if resolutions else "no usable video resolutions"
            return resolutions, time.monotonic() - started, status
        except Exception as exc:
            return set(), time.monotonic() - started, str(exc)

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


def _youtube_resource_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    video_id: str | None = None
    if host == "youtu.be" or host.endswith(".youtu.be"):
        video_id = next((part for part in parsed.path.split("/") if part), None)
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path.rstrip("/") == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [None])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0].lower() in {
                "shorts",
                "embed",
                "live",
            }:
                video_id = parts[1]
    if video_id:
        return f"video:{video_id}"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"url:{parsed.scheme.lower()}://{host}{parsed.path}{query}"


def youtube_video_resolutions(info: object) -> set[int]:
    if not isinstance(info, dict):
        return set()
    formats = info.get("formats")
    if not isinstance(formats, list):
        return set()
    resolutions: set[int] = set()
    for item in formats:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        if str(item.get("vcodec") or "").lower() in {"", "none"}:
            continue
        width = _positive_dimension(item.get("width"))
        height = _positive_dimension(item.get("height"))
        if width is not None and height is not None:
            resolutions.add(min(width, height))
    return resolutions


def _positive_dimension(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        dimension = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return dimension if dimension > 0 else None


youtube_proxy_manager = YouTubeProxyManager()

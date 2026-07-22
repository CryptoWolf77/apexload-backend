from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.routes import admin_instagram
from app.core.config import get_settings
from app.main import app
from app.services import instagram_safety_service as safety_module
from app.services.instagram_safety_service import InstagramSafetyService


@pytest.fixture
def safety_service(monkeypatch: pytest.MonkeyPatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "instagram_safety_mode_enabled", True)
    monkeypatch.setattr(settings, "instagram_failure_threshold", 3)
    monkeypatch.setattr(settings, "instagram_restriction_cooldown_hours", 72)
    monkeypatch.setattr(settings, "instagram_rate_limit_cooldown_hours", 24)
    monkeypatch.setattr(settings, "instagram_unknown_error_cooldown_minutes", 30)
    monkeypatch.setattr(settings, "instagram_max_concurrent_jobs", 2)
    monkeypatch.setattr(settings, "instagram_max_requests_per_minute", 100)
    monkeypatch.setattr(settings, "instagram_max_requests_per_hour", 1000)
    monkeypatch.setattr(safety_module, "safety_state_path", lambda: tmp_path / "instagram_safety_state.json")
    monkeypatch.setattr(safety_module, "send_admin_email", lambda *args, **kwargs: True)
    return InstagramSafetyService()


def test_cookie_health_failure_only_degrades_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
    safety_service: InstagramSafetyService,
) -> None:
    monkeypatch.setattr(
        safety_module,
        "check_instagram_cookies",
        lambda: {
            "is_valid": False,
            "message": "Instagram cookies look invalid.",
            "technical_reason": "cookies invalid during health check",
        },
    )

    result = safety_service.manual_check()
    decision = safety_service.begin_request()
    safety_service.finish_neutral(decision)

    status = safety_service.status()
    assert result["success"] is False
    assert status["mode"] == "degraded"
    assert status["health_status"] == "warning"
    assert status["health_reason_category"] == "cookies_warning"
    assert status["consecutive_failures"] == 0
    assert status["paused_until"] is None
    assert decision.allowed is True


def test_single_cookie_health_warning_does_not_trigger_long_pause(
    safety_service: InstagramSafetyService,
) -> None:
    safety_service.record_health_warning(
        {
            "is_valid": False,
            "technical_reason": "cookies invalidated during scheduled check",
        }
    )

    status = safety_service.status()
    assert status["mode"] == "degraded"
    assert status["paused_until"] is None
    assert status["consecutive_failures"] == 0


def test_repeated_real_login_failures_activate_safety_mode(
    safety_service: InstagramSafetyService,
) -> None:
    for _ in range(2):
        safety_service.finish_failure("login required")
        assert safety_service.status()["mode"] == "limited"

    safety_service.finish_failure("login required")
    status = safety_service.status()

    assert status["mode"] == "paused"
    assert status["reason_category"] == "login_required"
    assert status["consecutive_failures"] == 3
    assert status["paused_until"]


def test_repeated_real_rate_limit_failures_activate_safety_mode(
    safety_service: InstagramSafetyService,
) -> None:
    for _ in range(2):
        safety_service.finish_failure("HTTP Error 429: too many requests")
        assert safety_service.status()["mode"] == "limited"

    safety_service.finish_failure("HTTP Error 429: too many requests")
    status = safety_service.status()

    assert status["mode"] == "paused"
    assert status["reason_category"] == "rate_limited"
    assert status["consecutive_failures"] == 3
    assert status["paused_until"]


def test_successful_request_clears_cookie_health_warning(
    safety_service: InstagramSafetyService,
) -> None:
    safety_service.record_health_warning(
        {"is_valid": False, "technical_reason": "cookies invalid during health check"}
    )

    safety_service.finish_success()
    status = safety_service.status()

    assert status["mode"] == "active"
    assert status["health_status"] == "healthy"
    assert status["health_reason_category"] == ""
    assert status["consecutive_failures"] == 0


def test_successful_request_clears_stale_cookie_pause_when_safe(
    safety_service: InstagramSafetyService,
) -> None:
    safety_service._state.update(
        mode="paused",
        reason="Instagram requires a valid server-side session.",
        reason_category="cookies_invalid",
        paused_until=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
        consecutive_failures=1,
    )

    safety_service.finish_success()
    status = safety_service.status()

    assert status["mode"] == "active"
    assert status["reason_category"] == ""
    assert status["paused_until"] is None
    assert status["consecutive_failures"] == 0


def test_admin_clear_endpoint_resets_state_and_requires_auth(
    monkeypatch: pytest.MonkeyPatch,
    safety_service: InstagramSafetyService,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")
    monkeypatch.setattr(settings, "admin_api_token", "test-admin-key")
    monkeypatch.setattr(admin_instagram, "instagram_safety_service", safety_service)
    safety_service.finish_failure("login required")
    safety_service.finish_failure("login required")
    safety_service.finish_failure("login required")

    client = TestClient(app)
    unauthorized = client.post("/api/admin/instagram/safety/clear")
    authorized = client.post(
        "/api/admin/instagram/safety/clear",
        headers={"X-Admin-Key": "test-admin-key"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    body = authorized.json()
    assert body["success"] is True
    assert body["mode"] == "active"
    assert body["consecutive_failures"] == 0
    assert body["paused_until"] is None

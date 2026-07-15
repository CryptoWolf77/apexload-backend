from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.takedown_protection import takedown_submission_guard
from app.main import app
from app.models.takedown_models import TakedownRequest, validate_takedown_request
from app.services import legal_notifications


@pytest.fixture(autouse=True)
def reset_takedown_guard(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "legal_rate_limit_hour", 5)
    monkeypatch.setattr(settings, "legal_rate_limit_day", 15)
    monkeypatch.setattr(settings, "legal_min_form_seconds", 3)
    takedown_submission_guard.reset_for_tests()
    yield
    takedown_submission_guard.reset_for_tests()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def delivery_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.api.routes.takedown.deliver_takedown_report",
        lambda *args: True,
    )


def report_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "fullName": "Avery Rights Holder",
        "companyOrRightsHolder": "Avery Studio",
        "email": "avery@example.com",
        "reportType": "copyright",
        "originalWorkUrl": "https://example.com/original-work",
        "reportedUrlOrReference": "https://example.net/reported-copy",
        "explanation": "This report identifies the original work and the allegedly infringing material in enough detail for review.",
        "goodFaithConfirmed": True,
        "accuracyConfirmed": True,
        "authorityConfirmed": True,
        "electronicSignature": "Avery Rights Holder",
        "website": "",
        "formStartedAt": (datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
    }
    payload.update(overrides)
    return payload


def post(client: TestClient, payload: dict[str, object]):
    return client.post(
        "/v1/public/takedown",
        json=payload,
        headers={"Origin": "https://apexload.org"},
    )


def test_valid_copyright_report_returns_reference(client: TestClient, delivery_ok) -> None:
    response = post(client, report_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["referenceId"].startswith("APL-TD-")
    assert body["reference"] == body["referenceId"]
    assert response.headers["cache-control"] == "no-store"


def test_valid_privacy_report(client: TestClient, delivery_ok) -> None:
    assert post(client, report_payload(reportType="privacy")).status_code == 201


def test_current_website_field_aliases_are_accepted(client: TestClient, delivery_ok) -> None:
    payload = report_payload(
        companyName="Avery Studio",
        originalWorkReference="https://example.com/original-work",
        reportedReference="https://example.net/reported-copy",
        goodFaithAccepted=True,
        accuracyAuthorityAccepted=True,
        contactConsent=True,
        language="en",
    )
    for field in (
        "companyOrRightsHolder",
        "originalWorkUrl",
        "reportedUrlOrReference",
        "goodFaithConfirmed",
        "accuracyConfirmed",
        "authorityConfirmed",
    ):
        payload.pop(field, None)

    assert post(client, payload).status_code == 201


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("goodFaithConfirmed", False),
        ("accuracyConfirmed", False),
        ("authorityConfirmed", False),
    ],
)
def test_missing_declarations_are_rejected(
    client: TestClient,
    delivery_ok,
    field: str,
    value: object,
) -> None:
    assert post(client, report_payload(**{field: value})).status_code == 422


def test_invalid_email_is_rejected(client: TestClient, delivery_ok) -> None:
    assert post(client, report_payload(email="not-an-email")).status_code == 422


def test_invalid_report_type_is_rejected(client: TestClient, delivery_ok) -> None:
    assert post(client, report_payload(reportType="unknown")).status_code == 422


def test_missing_identifying_information_is_rejected(client: TestClient, delivery_ok) -> None:
    response = post(client, report_payload(originalWorkUrl="", reportedUrlOrReference=""))

    assert response.status_code == 422


def test_empty_or_oversized_explanation_is_rejected(client: TestClient, delivery_ok) -> None:
    assert post(client, report_payload(explanation="")).status_code == 422
    assert post(client, report_payload(explanation="x" * 5001)).status_code == 422


def test_oversized_http_body_is_rejected(client: TestClient, delivery_ok) -> None:
    response = post(client, report_payload(explanation="x" * 70000))

    assert response.status_code == 413


def test_honeypot_and_fast_submission_are_rejected(client: TestClient, delivery_ok) -> None:
    assert post(client, report_payload(website="https://spam.example")).status_code == 422
    assert post(client, report_payload(formStartedAt=datetime.now(UTC).isoformat())).status_code == 422


def test_control_characters_are_rejected_before_normalization(client: TestClient, delivery_ok) -> None:
    response = post(
        client,
        report_payload(explanation="A valid-looking explanation\r\nBcc: injected@example.com"),
    )

    assert response.status_code == 422


def test_duplicate_submission_is_rejected(client: TestClient, delivery_ok) -> None:
    payload = report_payload()

    assert post(client, payload).status_code == 201
    assert post(client, payload).status_code == 409


def test_hourly_rate_limit_is_enforced(
    client: TestClient,
    delivery_ok,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "legal_rate_limit_hour", 2)
    for number in range(2):
        assert post(client, report_payload(email=f"reporter{number}@example.com")).status_code == 201

    assert post(client, report_payload(email="third@example.com")).status_code == 429


def test_delivery_failure_does_not_return_success(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.takedown.deliver_takedown_report",
        lambda *args: False,
    )

    response = post(client, report_payload())

    assert response.status_code == 503
    assert "referenceId" not in response.json()


def test_unapproved_origin_is_rejected(client: TestClient, delivery_ok) -> None:
    response = client.post(
        "/v1/public/takedown",
        json=report_payload(),
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 403


def test_html_like_content_is_not_logged(
    client: TestClient,
    delivery_ok,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_explanation = "<script>alert('x')</script> with enough additional plain-text detail to satisfy validation safely."
    signature = "Sensitive Signature"
    with caplog.at_level(logging.INFO, logger="apexload.takedown"):
        response = post(
            client,
            report_payload(
                explanation=secret_explanation,
                electronicSignature=signature,
            ),
        )

    assert response.status_code == 201
    assert secret_explanation not in caplog.text
    assert signature not in caplog.text
    assert "example.net/reported-copy" not in caplog.text


def test_email_content_is_plain_text_and_escapes_html_like_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    report = validate_takedown_request(
        TakedownRequest.model_validate(
            report_payload(
                explanation="<script>alert('x')</script> with enough additional plain-text detail to satisfy validation safely.",
            )
        ),
        settings,
    )
    sent = []
    monkeypatch.setattr(legal_notifications, "legal_email_configured", lambda: True)
    monkeypatch.setattr(legal_notifications, "_send", lambda message: sent.append(message) or True)

    assert legal_notifications.deliver_takedown_report(report, "APL-TD-TEST", "a" * 64)
    assert len(sent) == 2
    assert sent[0].get_content_type() == "text/plain"
    assert "&lt;script&gt;" in sent[0].get_content()

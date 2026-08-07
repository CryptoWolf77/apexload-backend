import secrets

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _runtime_code() -> str:
    return secrets.token_urlsafe(32)


def test_correct_code_succeeds(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_code = _runtime_code()
    monkeypatch.setattr(
        get_settings(),
        "play_reviewer_access_code",
        f"  {configured_code}  ",
    )

    response = client.post(
        "/api/reviewer-access/verify",
        json={"code": f"  {configured_code}  "},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert response.headers["cache-control"] == "no-store"


def test_incorrect_code_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        get_settings(),
        "play_reviewer_access_code",
        _runtime_code(),
    )

    response = client.post(
        "/api/reviewer-access/verify",
        json={"code": _runtime_code()},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Reviewer access denied."}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("configured_code", ["", "   "])
def test_missing_server_configuration_does_not_allow_access(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    configured_code: str,
) -> None:
    monkeypatch.setattr(
        get_settings(),
        "play_reviewer_access_code",
        configured_code,
    )

    response = client.post(
        "/api/reviewer-access/verify",
        json={"code": _runtime_code()},
    )

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"

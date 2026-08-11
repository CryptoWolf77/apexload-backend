import pytest
from fastapi.testclient import TestClient

from app.api.routes import analyze as analyze_route
from app.api.routes import download as download_route
from app.main import app
from app.services.ytdlp_analyze_service import UnsupportedUrlError, YtDlpAnalyzeService
from app.utils.platform_detector import detect_platform


YOUTUBE_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/shorts/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
]


@pytest.mark.parametrize("url", YOUTUBE_URLS)
def test_youtube_urls_are_not_detected_as_supported(url: str) -> None:
    assert detect_platform(url) == "Unknown"


def test_supported_domain_in_youtube_query_does_not_bypass_rejection() -> None:
    assert (
        detect_platform("https://youtube.com/watch?next=https://x.com/user/status/1")
        == "Unknown"
    )


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.tiktok.com/@creator/video/1", "TikTok"),
        ("https://www.instagram.com/reel/example/", "Instagram"),
        ("https://www.snapchat.com/spotlight/example", "Snapchat"),
        ("https://x.com/creator/status/1", "X/Twitter"),
        ("https://www.facebook.com/watch/?v=1", "Facebook"),
        ("https://www.pinterest.com/pin/1/", "Pinterest"),
        ("https://www.reddit.com/r/videos/comments/example/", "Reddit"),
    ],
)
def test_remaining_platforms_stay_supported(url: str, platform: str) -> None:
    assert detect_platform(url) == platform


@pytest.mark.parametrize("url", YOUTUBE_URLS)
def test_analyze_rejects_youtube_before_extraction(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(_url: str):
        raise AssertionError("yt-dlp extraction must not run for an unsupported URL")

    monkeypatch.setattr(analyze_route.ytdlp_service, "analyze", fail_if_called)

    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"url": url})

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["code"] == "unsupported_url"


def test_analyze_service_rejects_youtube_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = YtDlpAnalyzeService()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("yt-dlp extraction must not run for an unsupported URL")

    monkeypatch.setattr(service, "_extract_info", fail_if_called)

    with pytest.raises(UnsupportedUrlError):
        service.analyze(YOUTUBE_URLS[0])


@pytest.mark.parametrize("url", YOUTUBE_URLS)
def test_download_rejects_youtube_before_job_creation(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("download jobs must not be created for unsupported URLs")

    monkeypatch.setattr(download_route.download_service, "create_job", fail_if_called)

    with TestClient(app) as client:
        response = client.post(
            "/api/download",
            json={
                "url": url,
                "selectedItems": [{"formatId": "720p", "type": "video"}],
                "premium": False,
                "noWatermark": False,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "jobId": "",
        "status": "failed",
        "message": "This link is not supported yet.",
        "errorCode": "unsupported_url",
    }

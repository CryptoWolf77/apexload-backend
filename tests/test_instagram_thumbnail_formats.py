from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import analyze as analyze_route
from app.main import app
from app.services.ytdlp_analyze_service import YtDlpAnalyzeService


INSTAGRAM_REEL_URL = "https://www.instagram.com/reel/realistic-example/"
INSTAGRAM_IMAGE_URL = "https://www.instagram.com/p/realistic-image/"
THUMBNAIL_URL = "https://cdn.example.com/preview.jpg"


def _video_info() -> dict:
    return {
        "id": "realistic-video",
        "title": "Realistic social video",
        "duration": 32,
        "thumbnail": THUMBNAIL_URL,
        "formats": [
            {
                "format_id": "480p",
                "width": 480,
                "height": 854,
                "vcodec": "h264",
                "ext": "mp4",
                "url": "https://cdn.example.com/video-480.mp4",
            },
            {
                "format_id": "720p",
                "width": 720,
                "height": 1280,
                "vcodec": "h264",
                "ext": "mp4",
                "url": "https://cdn.example.com/video-720.mp4",
            },
            {
                "format_id": "1080p",
                "width": 1080,
                "height": 1920,
                "vcodec": "h264",
                "ext": "mp4",
                "url": "https://cdn.example.com/video-1080.mp4",
            },
        ],
    }


def _instagram_video_response(monkeypatch: pytest.MonkeyPatch):
    service = YtDlpAnalyzeService()
    monkeypatch.setattr(
        service,
        "_extract_instagram_info",
        lambda _url: (_video_info(), "yt_dlp"),
    )
    return service.analyze(INSTAGRAM_REEL_URL)


def test_instagram_video_omits_thumbnail_and_preserves_video_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _instagram_video_response(monkeypatch)
    formats = {item.id: item for item in response.formats}

    assert response.platform == "Instagram"
    assert response.mediaType == "video"
    assert "thumbnail" not in formats
    assert all(item.label != "Thumbnail JPG" for item in response.formats)
    assert [item.id for item in response.formats] == [
        "480p",
        "720p",
        "1080p",
        "2160p",
        "mp3",
    ]
    assert formats["480p"].available is True
    assert formats["720p"].available is True
    assert formats["1080p"].available is True
    assert formats["2160p"].available is False
    assert formats["mp3"].available is True
    assert formats["mp3"].label == "MP3 Audio"


def test_instagram_image_formats_remain_on_the_image_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = YtDlpAnalyzeService()
    image_info = {
        "_type": "image",
        "id": "realistic-image",
        "title": "Realistic Instagram image post",
        "url": "https://cdn.example.com/original-image.jpg",
        "ext": "jpg",
        "width": 1440,
        "height": 1800,
    }
    monkeypatch.setattr(
        service,
        "_extract_instagram_info",
        lambda _url: (image_info, "instagram_html"),
    )
    monkeypatch.setattr(
        service,
        "debug_instagram_photo_extraction",
        lambda *_args, **_kwargs: {
            "bestImageUrl": image_info["url"],
            "bestImageSource": "display_url",
        },
    )

    response = service.analyze(INSTAGRAM_IMAGE_URL)

    assert response.mediaType == "image"
    assert response.thumbnail == image_info["url"]
    assert [item.id for item in response.formats] == [
        "original",
        "jpg",
        "png",
        "webp",
        "high_quality",
        "compressed",
    ]
    assert response.formats[0].label == "Original Image"
    assert response.formats[0].available is True


@pytest.mark.parametrize(
    "platform",
    ["Facebook", "TikTok", "X/Twitter"],
)
def test_other_standard_video_platforms_retain_thumbnail(platform: str) -> None:
    formats = YtDlpAnalyzeService()._video_formats(
        _video_info(),
        THUMBNAIL_URL,
        platform,
    )

    thumbnails = [item for item in formats if item.id == "thumbnail"]
    assert len(thumbnails) == 1
    assert thumbnails[0].label == "Thumbnail JPG"
    assert thumbnails[0].available is True


def test_snapchat_specific_video_branch_retains_thumbnail() -> None:
    formats = YtDlpAnalyzeService()._video_formats(
        {"duration": 12, "formats": []},
        THUMBNAIL_URL,
        "Snapchat",
    )

    assert [item.id for item in formats] == ["best", "mp3", "thumbnail"]
    assert formats[-1].label == "Thumbnail JPG"
    assert formats[-1].available is True


class _AlwaysAllowedInstagramSafety:
    def begin_request(self):
        return SimpleNamespace(allowed=True, acquired=False)

    def finish_success(self, _decision) -> None:
        return None


def test_public_analyze_response_omits_instagram_video_thumbnail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _instagram_video_response(monkeypatch)
    monkeypatch.setattr(analyze_route.ytdlp_service, "analyze", lambda _url: response)
    monkeypatch.setattr(
        analyze_route,
        "instagram_safety_service",
        _AlwaysAllowedInstagramSafety(),
    )

    api_response = TestClient(app).post(
        "/api/analyze",
        json={"url": INSTAGRAM_REEL_URL},
    )

    assert api_response.status_code == 200
    body = api_response.json()
    assert body["platform"] == "Instagram"
    assert body["mediaType"] == "video"
    assert all(item["id"] != "thumbnail" for item in body["formats"])
    assert all(item["label"] != "Thumbnail JPG" for item in body["formats"])

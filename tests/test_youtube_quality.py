from pathlib import Path

import pytest
import yt_dlp

from app.models.download_models import DownloadRequest, SelectedDownloadItem
from app.services import download_service as download_module
from app.services.download_service import DownloadJob, DownloadService
from app.services.youtube_error_classifier import (
    YouTubeErrorCode,
    YouTubeOperationError,
    YouTubeQualityMismatchError,
)
from app.services.ytdlp_analyze_service import YtDlpAnalyzeService


YOUTUBE_URL = "https://www.youtube.com/shorts/SybaN0KNRhY"


def make_item(format_id: str) -> SelectedDownloadItem:
    return SelectedDownloadItem(formatId=format_id, type="video")


def make_job(item: SelectedDownloadItem) -> DownloadJob:
    request = DownloadRequest(url=YOUTUBE_URL, selectedItems=[item])
    return DownloadJob("job_quality", request, "YouTube Shorts")


def selected_dimensions(
    service: DownloadService,
    target: int,
    width: int,
    height: int,
) -> tuple[int, int, str, str]:
    formats = [
        {
            "format_id": "low-avc",
            "url": "https://media.invalid/low",
            "ext": "mp4",
            "vcodec": "avc1.4d401e",
            "acodec": "none",
            "width": 480 if width <= height else 854,
            "height": 854 if width <= height else 480,
        },
        {
            # A literal-height selector can mistake this portrait rendition
            # for 1080p even though its short edge is only 608 pixels.
            "format_id": "misleading-height",
            "url": "https://media.invalid/misleading",
            "ext": "mp4",
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "width": 608,
            "height": 1080,
        },
        {
            "format_id": "target-vp9",
            "url": "https://media.invalid/vp9",
            "ext": "webm",
            "vcodec": "vp9",
            "acodec": "none",
            "width": width,
            "height": height,
        },
        {
            "format_id": "target-avc",
            "url": "https://media.invalid/avc",
            "ext": "mp4",
            "vcodec": "avc1.640028",
            "acodec": "none",
            "width": width,
            "height": height,
        },
        {
            "format_id": "audio-m4a",
            "url": "https://media.invalid/audio",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
        },
    ]
    ydl = yt_dlp.YoutubeDL(
        {"quiet": True, "format_sort": service._youtube_format_sort(target)}
    )
    info = {"formats": formats}
    ydl.sort_formats(info)
    selected = list(
        ydl._select_formats(
            info["formats"],
            ydl.build_format_selector(service._youtube_format_selector(target)),
        )
    )
    assert len(selected) == 1
    output = selected[0]
    return output["width"], output["height"], output["vcodec"], output["acodec"]


@pytest.mark.parametrize(
    ("target", "width", "height"),
    [
        pytest.param(1080, 1080, 1920, id="portrait-short-1080p"),
        pytest.param(1080, 1920, 1080, id="landscape-1080p"),
        pytest.param(720, 720, 1280, id="portrait-720p"),
        pytest.param(720, 1280, 720, id="landscape-720p"),
    ],
)
def test_youtube_selection_uses_short_edge_and_keeps_compatible_codecs(
    target: int,
    width: int,
    height: int,
) -> None:
    service = DownloadService()

    selected_width, selected_height, vcodec, acodec = selected_dimensions(
        service, target, width, height
    )

    assert (selected_width, selected_height) == (width, height)
    assert vcodec.startswith("avc1")
    assert acodec.startswith("mp4a")


def test_youtube_analyze_uses_the_same_short_edge_resolution() -> None:
    service = YtDlpAnalyzeService()
    formats = service._video_formats(
        {
            "formats": [
                {
                    "format_id": "portrait-avc",
                    "width": 1080,
                    "height": 1920,
                    "vcodec": "avc1.640028",
                    "ext": "mp4",
                }
            ]
        },
        "",
        "YouTube Shorts",
    )
    availability = {item.id: item.available for item in formats}

    assert availability["1080p"] is True
    assert availability["2160p"] is False


def test_youtube_download_options_use_res_sort_and_mp4_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = DownloadService()
    item = make_item("1080p")
    job = make_job(item)
    captured: dict = {}

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def download(self, _urls: list[str]) -> None:
            (tmp_path / "1080p.mp4").write_bytes(b"valid video")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        service,
        "_ffprobe_video_dimensions",
        lambda _path: (1080, 1920),
    )

    service._execute_ytdlp_item(
        job,
        item,
        tmp_path,
        YOUTUBE_URL,
        str(tmp_path / "1080p.%(ext)s"),
        "1080p",
        "video",
        False,
        5,
        "socks5://8.8.8.8:1080",
    )

    assert captured["format"] == "bv*+ba/b"
    assert captured["format_sort"] == [
        "res:1080",
        "+codec:avc:m4a",
        "fps",
        "br",
    ]
    assert captured["merge_output_format"] == "mp4"
    assert captured["postprocessors"] == [
        {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}
    ]
    assert len(job.files) == 1


def test_lower_resolution_output_is_deleted_before_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = DownloadService()
    item = make_item("1080p")
    job = make_job(item)

    class FakeYoutubeDL:
        def __init__(self, _options: dict) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def download(self, _urls: list[str]) -> None:
            (tmp_path / "1080p.mp4").write_bytes(b"wrong video")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        service,
        "_ffprobe_video_dimensions",
        lambda _path: (480, 854),
    )

    with pytest.raises(YouTubeQualityMismatchError):
        service._execute_ytdlp_item(
            job,
            item,
            tmp_path,
            YOUTUBE_URL,
            str(tmp_path / "1080p.%(ext)s"),
            "1080p",
            "video",
            False,
            5,
            "socks5://8.8.8.8:1080",
        )

    assert job.files == []
    assert list(tmp_path.iterdir()) == []


def test_quality_mismatch_retries_routes_then_returns_format_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = DownloadService()
    item = make_item("1080p")
    job = make_job(item)
    settings = download_module.get_settings()
    monkeypatch.setattr(settings, "youtube_proxy_direct_first", False)
    monkeypatch.setattr(settings, "youtube_proxy_max_job_attempts", 2)
    acquired: list[str] = []

    def acquire(*_args, **_kwargs) -> str:
        proxy = f"socks5://8.8.8.{len(acquired) + 1}:1080"
        acquired.append(proxy)
        return proxy

    def fail_quality(_proxy: str | None) -> None:
        output = tmp_path / "1080p.mp4"
        output.write_bytes(b"wrong video")
        monkeypatch.setattr(
            service,
            "_ffprobe_video_dimensions",
            lambda _path: (480, 854),
        )
        service._validated_youtube_video_outputs(item, [output])

    monkeypatch.setattr(download_module.youtube_proxy_manager, "acquire", acquire)
    monkeypatch.setattr(
        download_module.youtube_proxy_manager,
        "report_failure",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(YouTubeOperationError) as raised:
        service._run_youtube_with_failover(
            job,
            tmp_path,
            YOUTUBE_URL,
            fail_quality,
        )

    assert len(acquired) == 2
    assert raised.value.classification.code == YouTubeErrorCode.FORMAT_UNAVAILABLE
    assert list(tmp_path.iterdir()) == []


def test_non_youtube_selector_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DownloadService()
    monkeypatch.setattr(download_module.shutil, "which", lambda _name: "ffmpeg")

    selector = service._format_selector(make_item("1080p"), "Facebook")

    assert "[height<=1080]" in selector

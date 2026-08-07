from pathlib import Path

import pytest

from app.models.download_models import DownloadRequest, SelectedDownloadItem
from app.services import download_service as download_module
from app.services.download_service import DownloadJob, DownloadService
from app.services.youtube_error_classifier import YouTubeOperationError


def make_job(url: str, item: SelectedDownloadItem) -> DownloadJob:
    request = DownloadRequest(url=url, selectedItems=[item])
    return DownloadJob("job_test", request, download_module.detect_platform(url))


def assert_youtube_url_uses_proxy_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
) -> None:
    service = DownloadService()
    item = SelectedDownloadItem(formatId="720p", type="video")
    job = make_job(url, item)
    settings = download_module.get_settings()
    monkeypatch.setattr(settings, "youtube_proxy_enabled", True)
    monkeypatch.setattr(settings, "youtube_proxy_direct_first", False)
    acquired_urls: list[str] = []
    proxy = "socks5://8.8.8.8:1080"

    def acquire(request_url: str, *_args, **_kwargs) -> str:
        acquired_urls.append(request_url)
        return proxy

    monkeypatch.setattr(download_module.youtube_proxy_manager, "acquire", acquire)
    monkeypatch.setattr(download_module.youtube_proxy_manager, "report_success", lambda *_args, **_kwargs: None)

    def execute(*_args, **_kwargs) -> None:
        output = tmp_path / "720p.mp4"
        output.write_bytes(b"media")
        service._register_file(job, output, "video")

    monkeypatch.setattr(service, "_execute_ytdlp_item", execute)
    service._download_item(job, item, tmp_path, 5)
    assert download_module.detect_platform(url) == "YouTube Shorts"
    assert acquired_urls == [url]


def test_normal_youtube_url_uses_proxy_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert_youtube_url_uses_proxy_manager(
        monkeypatch,
        tmp_path,
        "https://www.youtube.com/watch?v=normal-video",
    )


def test_youtube_shorts_url_uses_proxy_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert_youtube_url_uses_proxy_manager(
        monkeypatch,
        tmp_path,
        "https://www.youtube.com/shorts/short-video",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/example/",
        "https://www.facebook.com/watch/?v=123",
        "https://www.tiktok.com/@user/video/123",
        "https://x.com/user/status/123",
        "https://www.snapchat.com/spotlight/example",
        "https://www.reddit.com/r/videos/comments/example?source=youtube.com",
    ],
)
def test_non_youtube_never_calls_proxy_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
) -> None:
    service = DownloadService()
    item = SelectedDownloadItem(formatId="720p", type="video")
    job = make_job(url, item)
    monkeypatch.setattr(download_module.youtube_proxy_manager, "acquire", lambda *_args, **_kwargs: pytest.fail("proxy manager called"))
    called = []
    monkeypatch.setattr(service, "_execute_ytdlp_item", lambda *args, **kwargs: called.append(True))
    monkeypatch.setattr(service, "_download_instagram_with_cli", lambda *args, **kwargs: called.append(True))
    service._download_item(job, item, tmp_path, 5)
    assert called == [True]


def test_youtube_proxy_is_locked_for_whole_attempt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = DownloadService()
    item = SelectedDownloadItem(formatId="1080p", type="video")
    job = make_job("https://www.youtube.com/shorts/example", item)
    proxy = "socks5://8.8.8.8:1080"
    monkeypatch.setattr(download_module.youtube_proxy_manager, "acquire", lambda *_args, **_kwargs: proxy)
    monkeypatch.setattr(download_module.youtube_proxy_manager, "report_success", lambda *_args, **_kwargs: None)

    def execute(*args, **kwargs):
        assert job.youtube_proxy == proxy
        assert args[-1] == proxy
        (tmp_path / "1080p.mp4").write_bytes(b"media")
        service._register_file(job, tmp_path / "1080p.mp4", "video")

    monkeypatch.setattr(service, "_execute_ytdlp_item", execute)
    service._download_item(job, item, tmp_path, 5)
    assert job.youtube_proxy == proxy
    assert len(job.files) == 1


def test_retry_limit_preserves_format_and_cleans_partial_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = DownloadService()
    item = SelectedDownloadItem(formatId="1080p", type="video")
    job = make_job("https://www.youtube.com/watch?v=example", item)
    settings = download_module.get_settings()
    monkeypatch.setattr(settings, "youtube_proxy_max_job_attempts", 3)
    proxies = iter([f"socks5://8.8.8.{number}:1080" for number in range(1, 4)])
    monkeypatch.setattr(download_module.youtube_proxy_manager, "acquire", lambda *_args, **_kwargs: next(proxies))
    monkeypatch.setattr(download_module.youtube_proxy_manager, "report_failure", lambda *_args, **_kwargs: None)
    attempted_formats: list[str] = []

    def fail(*args, **kwargs):
        attempted_formats.append(args[1].formatId)
        (tmp_path / f"attempt-{len(attempted_formats)}.part").write_bytes(b"partial")
        raise RuntimeError("connection refused")

    monkeypatch.setattr(service, "_execute_ytdlp_item", fail)
    with pytest.raises(YouTubeOperationError):
        service._download_item(job, item, tmp_path, 5)
    assert attempted_formats == ["1080p", "1080p", "1080p"]
    assert list(tmp_path.iterdir()) == []


def test_content_unavailable_is_verified_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = DownloadService()
    item = SelectedDownloadItem(formatId="720p", type="video")
    job = make_job("https://www.youtube.com/watch?v=removed", item)
    settings = download_module.get_settings()
    monkeypatch.setattr(settings, "youtube_proxy_max_job_attempts", 4)
    acquire_count = 0

    def acquire(*_args, **_kwargs):
        nonlocal acquire_count
        acquire_count += 1
        return f"socks5://8.8.8.{acquire_count}:1080"

    monkeypatch.setattr(download_module.youtube_proxy_manager, "acquire", acquire)
    monkeypatch.setattr(download_module.youtube_proxy_manager, "report_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_execute_ytdlp_item", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Video unavailable")))
    with pytest.raises(YouTubeOperationError) as raised:
        service._download_item(job, item, tmp_path, 5)
    assert acquire_count == 2
    assert raised.value.classification.code.value == "YOUTUBE_CONTENT_UNAVAILABLE"


def test_cleanup_never_removes_preexisting_file(tmp_path: Path) -> None:
    service = DownloadService()
    original = tmp_path / "original.keep"
    original.write_bytes(b"keep")
    baseline = set(tmp_path.iterdir())
    (tmp_path / "video.part").write_bytes(b"partial")
    service._cleanup_attempt_files(tmp_path, baseline)
    assert original.read_bytes() == b"keep"
    assert not (tmp_path / "video.part").exists()

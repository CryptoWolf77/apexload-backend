from pathlib import Path

import pytest

from app.models.download_models import DownloadRequest, SelectedDownloadItem
from app.services import tiktok_recovery, ytdlp_options
from app.services.download_service import DownloadJob, DownloadService
from app.services.tiktok_recovery import (
    TIKTOK_TEMPORARY_MESSAGE,
    TIKTOK_UNAVAILABLE_MESSAGE,
    TikTokOperationCancelled,
    TikTokRecoveryError,
    classify_tiktok_error,
    run_ytdlp_with_tiktok_recovery,
)
from app.services.ytdlp_analyze_service import AnalyzeServiceError, YtDlpAnalyzeService


TIKTOK_URL = "https://www.tiktok.com/@creator/video/7000000000000000001"


def test_tiktok_analyze_and_download_options_include_chrome_impersonation() -> None:
    analyze_options = ytdlp_options.build_ytdlp_options("TikTok", "analyze")
    download_options = ytdlp_options.build_ytdlp_options("TikTok", "download")

    assert str(analyze_options["impersonate"]) == "chrome"
    assert str(download_options["impersonate"]) == "chrome"


def test_instagram_options_remain_on_existing_auth_and_chrome_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ytdlp_options,
        "_instagram_auth_options",
        lambda: {"cookiefile": "instagram.txt"},
    )
    monkeypatch.setattr(
        ytdlp_options,
        "build_impersonate_target",
        lambda value: f"instagram-{value}",
    )

    options = ytdlp_options.build_ytdlp_options("Instagram", "analyze")

    assert options["cookiefile"] == "instagram.txt"
    assert options["impersonate"] == "instagram-chrome"


@pytest.mark.parametrize(
    "platform",
    ["Facebook", "X/Twitter", "Pinterest", "Reddit", "Snapchat"],
)
def test_other_platforms_do_not_receive_tiktok_impersonation(platform: str) -> None:
    assert "impersonate" not in ytdlp_options.build_ytdlp_options(platform, "analyze")


@pytest.mark.parametrize(
    ("message", "classification"),
    [
        ("Unexpected response from webpage request", "unexpected_webpage_response"),
        (
            "Unable to extract universal data for rehydration",
            "missing_rehydration_data",
        ),
        ("temporary TikTok webpage challenge failure", "challenge_response"),
        ("HTTP Error 403: Forbidden", "http_403"),
        ("HTTP Error 429: Too Many Requests", "http_429"),
    ],
)
def test_tiktok_transient_error_classification_is_platform_specific(
    message: str,
    classification: str,
) -> None:
    assert classify_tiktok_error("TikTok", message) == classification
    assert classify_tiktok_error("Instagram", message) is None


@pytest.mark.parametrize(
    "message",
    [
        "This video is private",
        "This video has been deleted",
        "Login required",
        "Unsupported URL",
        "This video is not available in your country",
        "Invalid video ID",
        "Operation cancelled by user",
    ],
)
def test_permanent_tiktok_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    instances = _install_fake_ytdlp(monkeypatch, [RuntimeError(message)])

    with pytest.raises(RuntimeError, match=message):
        _run_fake_recovery()

    assert len(instances) == 1


def test_transient_failure_uses_fresh_instance_and_succeeds_on_attempt_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_ytdlp(
        monkeypatch,
        [
            RuntimeError("Unable to extract universal data for rehydration"),
            {"id": "ok"},
        ],
    )

    result = _run_fake_recovery()

    assert result == {"id": "ok"}
    assert len(instances) == 2
    assert instances[0] is not instances[1]
    assert [instance.options["impersonate"] for instance in instances] == [
        "chrome",
        "chrome",
    ]


def test_transient_failure_succeeds_on_attempt_three_with_supported_safari(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_ytdlp(
        monkeypatch,
        [
            RuntimeError("HTTP Error 403: Forbidden"),
            RuntimeError("HTTP Error 429: Too Many Requests"),
            {"id": "ok"},
        ],
    )

    assert _run_fake_recovery() == {"id": "ok"}
    assert len(instances) == 3
    assert [instance.options["impersonate"] for instance in instances] == [
        "chrome",
        "chrome",
        "safari",
    ]


def test_maximum_attempts_is_three_and_final_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (
        "Unexpected response from webpage request; see "
        "https://github.com/yt-dlp/yt-dlp/issues/123"
    )
    instances = _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError(raw), RuntimeError(raw), RuntimeError(raw)],
    )

    with pytest.raises(TikTokRecoveryError) as error:
        _run_fake_recovery()

    assert len(instances) == 3
    assert error.value.attempts == 3
    assert str(error.value) == TIKTOK_TEMPORARY_MESSAGE
    assert "github.com" not in str(error.value)


def test_cancellation_after_first_failure_stops_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError("Unexpected response from webpage request")],
    )
    cancelled = False

    def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    with pytest.raises(TikTokOperationCancelled):
        _run_fake_recovery(
            cancellation_check=lambda: cancelled,
            before_retry=cancel,
        )

    assert len(instances) == 1


def test_unavailable_impersonation_support_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_ytdlp(monkeypatch, [{"id": "ok"}])
    monkeypatch.setattr(
        tiktok_recovery,
        "build_supported_impersonate_target",
        lambda _value: None,
    )

    assert _run_fake_recovery() == {"id": "ok"}
    assert "impersonate" not in instances[0].options


def test_analyze_uses_shared_recovery_and_sanitizes_raw_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ytdlp_analyze_service as analyze_module

    captured: dict = {}

    def fail_shared(**kwargs):
        captured.update(kwargs)
        raise RuntimeError(
            "Unexpected response from webpage request "
            "https://github.com/yt-dlp/yt-dlp/issues/123"
        )

    monkeypatch.setattr(
        analyze_module,
        "run_ytdlp_with_tiktok_recovery",
        fail_shared,
    )

    with pytest.raises(AnalyzeServiceError) as error:
        YtDlpAnalyzeService()._extract_info(TIKTOK_URL)

    assert captured["platform"] == "TikTok"
    assert captured["operation"] == "analyze"
    assert captured["purpose"] == "analyze"
    assert error.value.message == TIKTOK_TEMPORARY_MESSAGE
    assert "github.com" not in error.value.message


def test_tiktok_video_download_uses_shared_recovery_and_preserves_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services import download_service as download_module

    service = DownloadService()
    item = SelectedDownloadItem(formatId="720p", type="video")
    job = _job(item)
    captured: list[dict] = []
    expected_selector = service._format_selector(item, "TikTok")

    def fake_shared(**kwargs):
        captured.append(kwargs)
        output_path = Path(kwargs["extra_options"]["outtmpl"].replace("%(ext)s", "mp4"))
        output_path.write_bytes(b"video-bytes")
        return kwargs["action"](_NoOpYdl())

    monkeypatch.setattr(download_module, "run_ytdlp_with_tiktok_recovery", fake_shared)
    output_template = str(tmp_path / "720p.%(ext)s")

    for _ in range(2):
        service._execute_ytdlp_item(
            job,
            item,
            tmp_path,
            TIKTOK_URL,
            output_template,
            "720p",
            "video",
            False,
            5,
        )

    assert captured[0]["operation"] == "download"
    assert captured[0]["purpose"] == "download"
    assert captured[0]["extra_options"]["format"] == expected_selector
    assert len(job.files) == 1


def test_tiktok_mp3_download_keeps_audio_extraction_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services import download_service as download_module

    service = DownloadService()
    monkeypatch.setattr(service, "_ffmpeg_available", lambda: True)
    item = SelectedDownloadItem(formatId="mp3", type="audio")
    job = _job(item)
    captured: dict = {}

    def fake_shared(**kwargs):
        captured.update(kwargs)
        output_path = Path(kwargs["extra_options"]["outtmpl"].replace("%(ext)s", "mp3"))
        output_path.write_bytes(b"audio-bytes")
        return kwargs["action"](_NoOpYdl())

    monkeypatch.setattr(download_module, "run_ytdlp_with_tiktok_recovery", fake_shared)

    service._execute_ytdlp_item(
        job,
        item,
        tmp_path,
        TIKTOK_URL,
        str(tmp_path / "mp3.%(ext)s"),
        "mp3",
        "audio",
        True,
        5,
    )

    options = captured["extra_options"]
    assert options["format"] == "bestaudio/best"
    assert options["postprocessors"] == [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    ]
    assert options["keepvideo"] is False
    assert len(job.files) == 1


def test_retry_cleanup_removes_only_partial_or_empty_outputs(tmp_path: Path) -> None:
    service = DownloadService()
    valid = tmp_path / "720p.mp4"
    partial = tmp_path / "720p.mp4.part"
    state = tmp_path / "720p.mp4.ytdl"
    empty = tmp_path / "720p.webm"
    unrelated = tmp_path / "other.mp4.part"
    valid.write_bytes(b"complete")
    partial.write_bytes(b"partial")
    state.write_bytes(b"state")
    empty.touch()
    unrelated.write_bytes(b"unrelated")

    service._cleanup_tiktok_retry_artifacts(tmp_path, "720p")

    assert valid.is_file()
    assert unrelated.is_file()
    assert not partial.exists()
    assert not state.exists()
    assert not empty.exists()


def test_retry_progress_never_moves_backwards() -> None:
    service = DownloadService()
    item = SelectedDownloadItem(formatId="720p", type="video")
    job = _job(item)
    job.progress = 80

    service._progress_hook(job, 5)(
        {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100}
    )

    assert job.progress == 80


def test_progress_hook_stops_an_inflight_cancelled_job() -> None:
    service = DownloadService()
    item = SelectedDownloadItem(formatId="720p", type="video")
    job = _job(item)
    job.status = "cancelled"

    with pytest.raises(TikTokOperationCancelled):
        service._progress_hook(job, 5)(
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100}
        )


def test_download_error_never_returns_raw_extractor_or_github_text() -> None:
    service = DownloadService()
    raw = RuntimeError(
        "Unexpected response from webpage request "
        "https://github.com/yt-dlp/yt-dlp/issues/123"
    )

    message = service._download_error_message(TIKTOK_URL, raw)

    assert message == TIKTOK_TEMPORARY_MESSAGE
    assert "github.com" not in message
    assert service._error_code_for(RuntimeError(message)) == (
        "TIKTOK_TEMPORARILY_UNAVAILABLE"
    )
    assert service._download_error_message(
        TIKTOK_URL,
        RuntimeError("This video is private"),
    ) == TIKTOK_UNAVAILABLE_MESSAGE


def _install_fake_ytdlp(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[object],
) -> list[object]:
    import yt_dlp

    instances: list[object] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            self.options = options
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def extract_info(self, _url: str, download: bool = False):
            assert download is False
            outcome = outcomes[len(instances) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        tiktok_recovery,
        "build_supported_impersonate_target",
        lambda value: value,
    )

    def fake_options(_platform: str, purpose: str, extra: dict | None) -> dict:
        options = {"purpose": purpose, **(extra or {})}
        target = tiktok_recovery.build_supported_impersonate_target("chrome")
        if target is not None:
            options["impersonate"] = target
        return options

    monkeypatch.setattr(
        tiktok_recovery,
        "build_ytdlp_options",
        fake_options,
    )
    return instances


def _run_fake_recovery(**overrides):
    arguments = {
        "platform": "TikTok",
        "operation": "analyze",
        "purpose": "analyze",
        "url": TIKTOK_URL,
        "extra_options": None,
        "action": lambda ydl: ydl.extract_info(TIKTOK_URL, download=False),
        "sleep_func": lambda _delay: None,
        "jitter_func": lambda _start, _end: 0.0,
    }
    arguments.update(overrides)
    return run_ytdlp_with_tiktok_recovery(**arguments)


def _job(item: SelectedDownloadItem) -> DownloadJob:
    request = DownloadRequest(
        url=TIKTOK_URL,
        selectedItems=[item],
        premium=False,
        noWatermark=False,
    )
    return DownloadJob("job_tiktok", request, "TikTok")


class _NoOpYdl:
    def download(self, _urls: list[str]) -> None:
        return None

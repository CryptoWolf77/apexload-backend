import json
import logging
from pathlib import Path

import pytest

from app.models.download_models import DownloadRequest, SelectedDownloadItem
from app.services import download_service as download_module
from app.services import (
    tiktok_diagnostic,
    tiktok_recovery,
    ytdlp_analyze_service,
    ytdlp_options,
)
from app.services.download_service import DownloadJob, DownloadService
from app.services.tiktok_embed_service import (
    TikTokEmbedError,
    TikTokEmbedMedia,
    TikTokEmbedSecurityError,
    TikTokEmbedService,
    is_allowed_tiktok_cdn_host,
    parse_tiktok_embed_html,
    parse_tiktok_video_id,
    validate_tiktok_media_url,
)
from app.services.tiktok_recovery import (
    TIKTOK_TEMPORARY_MESSAGE,
    TikTokOperationCancelled,
    TikTokRecoveryError,
    run_ytdlp_with_tiktok_recovery,
    sanitized_tiktok_video_id,
)
from app.services.ytdlp_analyze_service import (
    AnalyzeServiceError,
    YtDlpAnalyzeService,
)


VIDEO_ID = "7000000000000000001"
TIKTOK_URL = f"https://www.tiktok.com/@creator/video/{VIDEO_ID}"
EMPTY_CREATOR_TIKTOK_URL = f"https://www.tiktok.com/@/video/{VIDEO_ID}/"
MEDIA_URL = (
    "https://v16-webapp-prime.us.tiktok.com/video/tos/useast2a/public.mp4"
    "?token=top-secret-signature"
)
THUMBNAIL_URL = "https://p16-sign.tiktokcdn-us.com/obj/public-cover.jpeg"


def _public_resolver(host: str, port: int, **_kwargs):
    assert host
    assert port == 443
    return [(2, 1, 6, "", ("93.184.216.34", port))]


EMBED_HTML = f"""
<html>
  <head><meta property="og:description" content="Public TikTok title"></head>
  <body>
    <script id="__MODERN_ROUTER_DATA__" type="application/json">
      {{
        "loaderData": {{
          "videoData": {{
            "id": "{VIDEO_ID}",
            "description": "Public TikTok title",
            "video": {{
              "playAddr": "{MEDIA_URL.replace('&', '&amp;')}",
              "cover": "{THUMBNAIL_URL}",
              "width": 720,
              "height": 1280
            }}
          }}
        }}
      }}
    </script>
  </body>
</html>
"""


def test_tiktok_options_include_impersonation_and_explicit_user_agent() -> None:
    for purpose in ("analyze", "validate", "metadata", "download"):
        options = ytdlp_options.build_ytdlp_options("TikTok", purpose)
        assert str(options["impersonate"]) == "chrome"
        assert options["http_headers"]["User-Agent"] == (
            ytdlp_options.TIKTOK_CHROME_USER_AGENT
        )


def test_tiktok_extra_headers_cannot_remove_user_agent_or_impersonation() -> None:
    options = ytdlp_options.build_ytdlp_options(
        "TikTok",
        "download",
        {"http_headers": {"Referer": "https://www.tiktok.com/"}, "impersonate": None},
    )

    assert str(options["impersonate"]) == "chrome"
    assert options["http_headers"] == {
        "Referer": "https://www.tiktok.com/",
        "User-Agent": ytdlp_options.TIKTOK_CHROME_USER_AGENT,
    }


def test_all_tiktok_retry_attempts_keep_explicit_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_ytdlp(
        monkeypatch,
        [
            RuntimeError("Unexpected response from webpage request"),
            RuntimeError("Unexpected response from webpage request"),
            {"id": VIDEO_ID},
        ],
    )
    options: list[dict] = []

    assert _run_recovery(options_callback=lambda value: options.append(value)) == {
        "id": VIDEO_ID
    }
    assert len(options) == 3
    assert all(
        item["http_headers"]["User-Agent"]
        == ytdlp_options.TIKTOK_CHROME_USER_AGENT
        for item in options
    )
    assert str(options[0]["impersonate"]) == "chrome"
    assert str(options[2]["impersonate"]) in {"chrome", "safari"}


def test_instagram_and_other_platform_options_do_not_get_tiktok_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ytdlp_options, "_instagram_auth_options", lambda: {})
    instagram = ytdlp_options.build_ytdlp_options("Instagram", "analyze")

    assert "http_headers" not in instagram
    for platform in ("Facebook", "X/Twitter", "Pinterest", "Reddit", "Snapchat"):
        assert "http_headers" not in ytdlp_options.build_ytdlp_options(
            platform, "analyze"
        )


def test_normal_success_never_uses_embed_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    instances = _install_fake_ytdlp(monkeypatch, [{"id": VIDEO_ID}])
    fallback_calls = 0

    def fallback(_error):
        nonlocal fallback_calls
        fallback_calls += 1
        raise AssertionError("embed fallback must not run")

    result = _run_recovery(embed_fallback=fallback)

    assert result == {"id": VIDEO_ID}
    assert len(instances) == 1
    assert fallback_calls == 0


@pytest.mark.parametrize("success_attempt", [2, 3])
def test_retry_success_never_uses_embed_fallback(
    monkeypatch: pytest.MonkeyPatch,
    success_attempt: int,
) -> None:
    outcomes = [
        RuntimeError("Unexpected response from webpage request")
        for _ in range(success_attempt - 1)
    ] + [{"id": VIDEO_ID}]
    instances = _install_fake_ytdlp(monkeypatch, outcomes)
    fallback_calls = 0

    def fallback(_error):
        nonlocal fallback_calls
        fallback_calls += 1
        raise AssertionError("embed fallback must not run")

    assert _run_recovery(embed_fallback=fallback) == {"id": VIDEO_ID}
    assert len(instances) == success_attempt
    assert fallback_calls == 0


def test_challenge_after_three_attempts_invokes_embed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError("Unexpected response from webpage request")] * 3,
    )
    received: list[TikTokRecoveryError] = []

    result = _run_recovery(
        embed_fallback=lambda error: received.append(error) or {"id": VIDEO_ID}
    )

    assert result == {"id": VIDEO_ID}
    assert len(instances) == 3
    assert len(received) == 1
    assert received[0].attempts == 3


def test_embed_parser_extracts_public_metadata_and_signed_media() -> None:
    media = parse_tiktok_embed_html(
        EMBED_HTML,
        VIDEO_ID,
        resolver=_public_resolver,
    )

    assert media.video_id == VIDEO_ID
    assert media.media_url == MEDIA_URL
    assert media.media_host == "v16-webapp-prime.us.tiktok.com"
    assert media.title == "Public TikTok title"
    assert media.thumbnail == THUMBNAIL_URL
    assert (media.width, media.height) == (720, 1280)


def test_embed_service_uses_official_player_metadata_when_page_has_no_media() -> None:
    metadata = {
        "results": [{"id_str": VIDEO_ID, "code": "ok"}],
        "items": [
            {
                "id_str": VIDEO_ID,
                "desc": "Public TikTok title",
                "video_info": {
                    "width": 720,
                    "height": 1280,
                    "cover": {"url_list": [THUMBNAIL_URL]},
                    "play_addr": {"url_list": [MEDIA_URL]},
                },
            }
        ],
    }
    requested_urls: list[str] = []
    responses = [
        _Response(200, text="<html><body>Official player</body></html>"),
        _Response(200, text=json.dumps(metadata)),
    ]

    def request(url: str, **_kwargs):
        requested_urls.append(url)
        return responses.pop(0)

    service = TikTokEmbedService(requester=request, resolver=_public_resolver)

    media = service.resolve(TIKTOK_URL)

    assert media == _media()
    assert requested_urls[0] == f"https://www.tiktok.com/player/v1/{VIDEO_ID}"
    assert requested_urls[1].startswith(
        "https://www.tiktok.com/player/api/v1/items?"
    )
    assert f"item_ids={VIDEO_ID}" in requested_urls[1]


def test_embed_metadata_must_confirm_requested_public_video_id() -> None:
    metadata = {
        "results": [{"id_str": VIDEO_ID, "code": "private"}],
        "items": [{"id_str": VIDEO_ID, "play_addr": MEDIA_URL}],
    }
    responses = [
        _Response(200, text="<html><body>Official player</body></html>"),
        _Response(200, text=json.dumps(metadata)),
    ]
    service = TikTokEmbedService(
        requester=lambda *_args, **_kwargs: responses.pop(0),
        resolver=_public_resolver,
    )

    with pytest.raises(TikTokEmbedError, match="not available to embed"):
        service.resolve(TIKTOK_URL)


def test_embed_redirect_cannot_change_requested_video_id() -> None:
    other_id = "7000000000000000002"
    service = TikTokEmbedService(
        requester=lambda *_args, **_kwargs: _Response(
            302,
            headers={"Location": f"https://www.tiktok.com/player/v1/{other_id}"},
        ),
        resolver=_public_resolver,
    )

    with pytest.raises(TikTokEmbedSecurityError, match="changed the requested"):
        service.resolve(TIKTOK_URL)


@pytest.mark.parametrize(
    "body",
    ["<html></html>", "<script type='application/json'>{invalid}</script>"],
)
def test_missing_or_invalid_embed_data_fails_safely(body: str) -> None:
    with pytest.raises(TikTokEmbedError, match="did not expose"):
        parse_tiktok_embed_html(body, VIDEO_ID, resolver=_public_resolver)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@creator/video/123",
        "https://www.tiktok.com/t/ZShortCode/",
        "https://www.tiktokv.com/share/video/7000000000000000001/",
        "https://example.com/@creator/video/7000000000000000001",
        "http://www.tiktok.com/@creator/video/7000000000000000001",
        "file://www.tiktok.com/@creator/video/7000000000000000001",
        "https://user:password@www.tiktok.com/@creator/video/7000000000000000001",
    ],
)
def test_public_video_id_validation_rejects_malformed_urls(url: str) -> None:
    with pytest.raises(TikTokEmbedError):
        parse_tiktok_video_id(url)


@pytest.mark.parametrize(
    "message",
    ["This video is private", "This video has been deleted", "Login required"],
)
def test_permanent_failures_never_invoke_embed(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    _install_fake_ytdlp(monkeypatch, [RuntimeError(message)])
    called = False

    def fallback(_error):
        nonlocal called
        called = True

    with pytest.raises(RuntimeError, match=message):
        _run_recovery(embed_fallback=fallback)
    assert called is False


def test_cancellation_never_invokes_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_ytdlp(monkeypatch, [TikTokOperationCancelled("cancelled by user")])
    called = False

    def fallback(_error):
        nonlocal called
        called = True

    with pytest.raises(TikTokOperationCancelled):
        _run_recovery(embed_fallback=fallback)
    assert called is False


def test_malformed_tiktok_url_never_invokes_embed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError("Unexpected response from webpage request")] * 3,
    )
    embed = _EmbedFake()
    monkeypatch.setattr(ytdlp_analyze_service, "tiktok_embed_service", embed)

    with pytest.raises(AnalyzeServiceError, match="temporarily rejected"):
        YtDlpAnalyzeService()._extract_info("https://www.tiktok.com/t/ZShortCode/")

    assert embed.resolve_calls == 0


@pytest.mark.parametrize(
    "host",
    [
        "v16-webapp-prime.us.tiktok.com",
        "p16-sign.tiktokcdn-us.com",
        "v16.tiktokv.com",
        "v16.byteoversea.com",
    ],
)
def test_expected_tiktok_cdn_hosts_are_allowed(host: str) -> None:
    assert is_allowed_tiktok_cdn_host(host)


@pytest.mark.parametrize(
    "url",
    [
        "http://v16.tiktokcdn.com/video.mp4",
        "file:///tmp/video.mp4",
        "https://evil.example/video.mp4",
        "https://tiktokcdn.com.evil.example/video.mp4",
        "https://127.0.0.1/video.mp4",
    ],
)
def test_media_url_https_and_host_allowlist_is_enforced(url: str) -> None:
    with pytest.raises(TikTokEmbedSecurityError):
        validate_tiktok_media_url(url, resolver=_public_resolver)


def test_private_dns_target_is_rejected() -> None:
    def private_resolver(_host: str, port: int, **_kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", port))]

    with pytest.raises(TikTokEmbedSecurityError, match="non-public"):
        validate_tiktok_media_url(MEDIA_URL, resolver=private_resolver)


def test_redirect_to_private_ip_is_rejected(tmp_path: Path) -> None:
    responses = [_Response(302, headers={"Location": "https://127.0.0.1/private"})]
    service = TikTokEmbedService(
        requester=lambda *_args, **_kwargs: responses.pop(0),
        resolver=_public_resolver,
    )

    with pytest.raises(TikTokEmbedSecurityError):
        service.download(_media(), tmp_path / "video.mp4")
    assert not (tmp_path / "video.mp4").exists()


def test_signed_media_url_is_never_logged_in_full(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = TikTokEmbedService(
        requester=lambda *_args, **_kwargs: _Response(200, text=EMBED_HTML),
        resolver=_public_resolver,
    )

    with caplog.at_level(logging.INFO):
        media = service.resolve(TIKTOK_URL)

    assert media.media_url == MEDIA_URL
    assert "top-secret-signature" not in caplog.text
    assert MEDIA_URL not in caplog.text
    assert media.media_host in caplog.text


@pytest.mark.parametrize("url", [TIKTOK_URL, EMPTY_CREATOR_TIKTOK_URL])
def test_internal_diagnostic_matches_production_tiktok_id_parsing(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    class DiagnosticYdl:
        def extract_info(self, _url: str, download: bool = False):
            assert download is False
            return {"formats": [{"url": MEDIA_URL}]}

    monkeypatch.setattr(
        tiktok_diagnostic,
        "run_ytdlp_with_tiktok_recovery",
        lambda **kwargs: kwargs["action"](DiagnosticYdl()),
    )

    assert parse_tiktok_video_id(url) == sanitized_tiktok_video_id(url) == VIDEO_ID

    result = tiktok_diagnostic.diagnose_tiktok_url(url)
    rendered = json.dumps(result)

    assert result["normalExtraction"] == "succeeded"
    assert result["resolvedMediaHost"] == "v16-webapp-prime.us.tiktok.com"
    assert "top-secret-signature" not in rendered
    assert MEDIA_URL not in rendered


def test_analyze_challenge_uses_embed_and_advertises_one_real_rendition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError("Unexpected response from webpage request")] * 3,
    )
    monkeypatch.setattr(ytdlp_analyze_service, "tiktok_embed_service", _EmbedFake())

    response = YtDlpAnalyzeService().analyze(TIKTOK_URL)

    assert response.success is True
    assert response.source == "tiktok_embed"
    video_formats = [item for item in response.formats if item.type == "video"]
    assert [(item.id, item.quality) for item in video_formats] == [("best", "720p")]
    assert not any(item.id in {"480p", "1080p", "2160p"} for item in response.formats)


def test_mocked_end_to_end_mp4_fallback_completes_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = DownloadService()
    embed = _EmbedFake()
    monkeypatch.setattr(download_module, "tiktok_embed_service", embed)
    _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError("Unexpected response from webpage request")] * 6,
    )
    item = SelectedDownloadItem(formatId="720p", type="video")
    job = _job(item)
    template = str(tmp_path / "720p.%(ext)s")

    for _ in range(2):
        service._execute_ytdlp_item(
            job,
            item,
            tmp_path,
            TIKTOK_URL,
            template,
            "720p",
            "video",
            False,
            5,
        )

    assert (tmp_path / "720p.mp4").read_bytes() == b"public-video"
    assert len(job.files) == 1
    assert job.files[0].type == "video"
    assert job.progress == 65
    assert embed.download_calls == 2


def test_mp3_fallback_uses_existing_ffmpeg_conversion_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = DownloadService()
    embed = _EmbedFake()
    monkeypatch.setattr(download_module, "tiktok_embed_service", embed)
    monkeypatch.setattr(service, "_ffmpeg_available", lambda: True)
    _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError("Unexpected response from webpage request")] * 3,
    )
    conversion_calls: list[tuple[Path, Path]] = []

    def convert(_job, _item, source: Path, output: Path) -> None:
        assert source.read_bytes() == b"public-video"
        conversion_calls.append((source, output))
        output.write_bytes(b"converted-mp3")

    monkeypatch.setattr(service, "_convert_tiktok_embed_audio", convert)
    item = SelectedDownloadItem(formatId="mp3", type="audio")
    job = _job(item)

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

    assert len(conversion_calls) == 1
    assert (tmp_path / "mp3.mp3").read_bytes() == b"converted-mp3"
    assert not (tmp_path / "mp3.source.mp4").exists()
    assert len(job.files) == 1
    assert job.files[0].type == "audio"


def test_failed_embed_fallback_returns_existing_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_ytdlp(
        monkeypatch,
        [RuntimeError("Unexpected response from webpage request")] * 3,
    )

    with pytest.raises(TikTokRecoveryError) as error:
        _run_recovery(
            embed_fallback=lambda _error: (_ for _ in ()).throw(
                RuntimeError(
                    "https://github.com/yt-dlp/yt-dlp/issues/123?token=secret"
                )
            )
        )

    assert str(error.value) == TIKTOK_TEMPORARY_MESSAGE
    assert "github.com" not in str(error.value)
    assert "secret" not in str(error.value)


def _install_fake_ytdlp(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[object],
) -> list[object]:
    import yt_dlp

    instances: list[object] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            self.options = options
            self.index = len(instances)
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def extract_info(self, _url: str, download: bool = False):
            assert download is False
            return self._outcome()

        def download(self, _urls: list[str]):
            return self._outcome()

        def _outcome(self):
            outcome = outcomes[self.index]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(tiktok_recovery.time, "sleep", lambda _delay: None)
    return instances


def _run_recovery(**overrides):
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


def _media() -> TikTokEmbedMedia:
    return TikTokEmbedMedia(
        video_id=VIDEO_ID,
        media_url=MEDIA_URL,
        media_host="v16-webapp-prime.us.tiktok.com",
        title="Public TikTok title",
        thumbnail=THUMBNAIL_URL,
        width=720,
        height=1280,
    )


class _EmbedFake:
    def __init__(self) -> None:
        self.resolve_calls = 0
        self.download_calls = 0

    def resolve(self, url: str) -> TikTokEmbedMedia:
        assert url == TIKTOK_URL
        self.resolve_calls += 1
        return _media()

    def download(
        self,
        _media_value: TikTokEmbedMedia,
        target: Path,
        *,
        progress_callback=None,
        cancellation_check=None,
    ) -> Path:
        assert not cancellation_check or not cancellation_check()
        self.download_calls += 1
        if not target.exists():
            target.write_bytes(b"public-video")
        if progress_callback:
            progress_callback(80, 100)
        return target


class _Response:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        text: str = "",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self._chunks = chunks or []
        self.closed = False

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def _job(item: SelectedDownloadItem) -> DownloadJob:
    request = DownloadRequest(
        url=TIKTOK_URL,
        selectedItems=[item],
        premium=False,
        noWatermark=False,
    )
    return DownloadJob("job_embed", request, "TikTok")

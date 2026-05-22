import logging
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from app.models.download_models import (
    DownloadFile,
    DownloadRequest,
    DownloadStartResponse,
    DownloadStatusResponse,
    SelectedDownloadItem,
)
from app.services.instagram_auth_service import (
    InstagramAuthError,
    get_instagram_auth_status,
)
from app.services.ytdlp_analyze_service import (
    INSTAGRAM_PHOTO_UNAVAILABLE_MESSAGE,
    YtDlpAnalyzeService,
)
from app.services.ytdlp_options import (
    build_ytdlp_options,
    configured_instagram_cookiefile,
)
from app.utils.platform_detector import detect_platform

logger = logging.getLogger("apexload.download")

DOWNLOAD_ROOT = Path("storage/downloads")
MIN_IMAGE_BYTES = 10 * 1024


class DownloadJob:
    def __init__(self, job_id: str, request: DownloadRequest, platform: str) -> None:
        self.job_id = job_id
        self.request = request
        self.platform = platform
        self.status = "queued"
        self.progress = 0
        self.message = "Download job created"
        self.error: str | None = None
        self.files: list[DownloadFile] = []


class DownloadService:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._files: dict[str, Path] = {}
        self._lock = threading.Lock()
        self._analyze_service = YtDlpAnalyzeService()
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    def create_job(self, request: DownloadRequest) -> DownloadStartResponse:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        platform = detect_platform(request.url)
        job = DownloadJob(job_id=job_id, request=request, platform=platform)
        with self._lock:
            self._jobs[job_id] = job
        logger.info("Download job created. job_id=%s platform=%s", job_id, platform)
        return DownloadStartResponse(
            success=True,
            jobId=job_id,
            status="queued",
            message="Download job created",
        )

    def process_job(self, job_id: str) -> None:
        job = self._get_job(job_id)
        if job is None:
            logger.error("Download job not found. job_id=%s", job_id)
            return

        job_dir = DOWNLOAD_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        self._update_job(job, status="processing", progress=5, message="Download started")
        logger.info("Download started. job_id=%s output_path=%s", job_id, job_dir)

        try:
            if not job.request.selectedItems:
                raise ValueError("No download option selected")

            for index, item in enumerate(job.request.selectedItems, start=1):
                logger.info(
                    "Selected format. job_id=%s format=%s type=%s",
                    job_id,
                    item.formatId,
                    item.type,
                )
                base_progress = 5 + int(((index - 1) / len(job.request.selectedItems)) * 85)
                try:
                    self._download_item(job, item, job_dir, base_progress)
                except Exception as item_exc:
                    logger.exception(
                        "Selected item download failed. job_id=%s format=%s",
                        job_id,
                        item.formatId,
                    )
                    job.error = self._safe_error(item_exc)

            if not job.files:
                raise RuntimeError(job.error or "Download failed")
            message = (
                "Some selected items could not be downloaded."
                if job.error
                else "Download completed"
            )
            self._update_job(
                job,
                status="completed",
                progress=100,
                message=message,
            )
            logger.info("Download completed. job_id=%s files=%s", job_id, len(job.files))
        except Exception as exc:
            logger.exception("Download failed. job_id=%s", job_id)
            self._update_job(
                job,
                status="failed",
                progress=0,
                message="Download failed",
                error=self._safe_error(exc),
            )

    def get_status(self, job_id: str) -> DownloadStatusResponse:
        job = self._get_job(job_id)
        if job is None:
            return DownloadStatusResponse(
                success=False,
                jobId=job_id,
                status="failed",
                progress=0,
                platform=None,
                message="Download job not found",
                files=[],
                error="Download job not found",
            )
        with self._lock:
            return DownloadStatusResponse(
                success=job.status != "failed",
                jobId=job.job_id,
                status=job.status,
                progress=job.progress,
                platform=job.platform,
                message=job.message,
                files=list(job.files),
                error=job.error,
            )

    def get_file_path(self, file_id: str) -> Path | None:
        with self._lock:
            path = self._files.get(file_id)
        if path and path.is_file():
            return path
        return None

    def _download_item(
        self,
        job: DownloadJob,
        item: SelectedDownloadItem,
        job_dir: Path,
        base_progress: int,
    ) -> None:
        format_id = self._format_id(item)
        item_type = self._item_type(item)
        self._update_job(
            job,
            status="processing",
            progress=max(base_progress, 10),
            message=f"Downloading {format_id}",
        )
        if item_type == "image" or format_id in {
            "thumbnail",
            "jpg",
            "png",
            "webp",
            "original",
            "high_quality",
            "compressed",
        }:
            self._download_image_or_thumbnail(job, item, job_dir)
            return

        download_url = self._download_url(job.request.url)
        is_audio_item = self._is_audio_item(item)
        output_name = (
            self._audio_codec(item)
            if is_audio_item
            else self._sanitize_filename(format_id)
        )
        output_template = str(job_dir / f"{output_name}.%(ext)s")
        platform = detect_platform(job.request.url)
        if platform == "Instagram":
            self._download_instagram_with_cli(
                job,
                item,
                job_dir,
                download_url,
                output_template,
                item_type,
            )
            return

        options = {
            "outtmpl": output_template,
            "format": self._format_selector(item, platform),
            "progress_hooks": [self._progress_hook(job, base_progress)],
        }
        try:
            options = build_ytdlp_options(
                platform,
                "download",
                options,
            )
        except InstagramAuthError as exc:
            raise RuntimeError(
                self._download_error_message(job.request.url, exc, item)
            ) from exc
        if is_audio_item:
            self._configure_audio_options(options, item)

        self._ensure_instagram_download_auth(job.request.url, item, options)

        try:
            import yt_dlp

            before = set(job_dir.iterdir())
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([download_url])
            after = set(job_dir.iterdir())
        except InstagramAuthError as exc:
            raise RuntimeError(
                self._download_error_message(job.request.url, exc, item)
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                self._download_error_message(job.request.url, exc, item)
            ) from exc

        new_files = [path for path in after - before if path.is_file()]
        if not new_files:
            new_files = sorted(job_dir.glob(f"{output_name}.*"))
        if is_audio_item:
            new_files = self._validated_audio_outputs(job_dir, item, new_files)
        for path in new_files:
            self._register_file(job, path, "audio" if is_audio_item else item_type)

    def _download_image_or_thumbnail(
        self,
        job: DownloadJob,
        item: SelectedDownloadItem,
        job_dir: Path,
    ) -> None:
        info: dict | None = None
        try:
            info = self._extract_info(job.request.url)
        except RuntimeError as exc:
            if not self._should_try_image_html_fallback(job.request.url, exc):
                raise
            logger.info("Image metadata fallback after yt-dlp image error")

        image_url = None
        if info:
            media_info = self._analyze_service._select_media_info(info)
            if detect_platform(job.request.url) == "Instagram":
                photo_debug = self._analyze_service.debug_instagram_photo_extraction(
                    self._download_url(job.request.url),
                    info,
                    configured_instagram_cookiefile(),
                )
                image_url = (
                    photo_debug["bestImageUrl"]
                    if isinstance(photo_debug.get("bestImageUrl"), str)
                    else None
                )
                source = str(photo_debug.get("bestImageSource") or "instagram_photo")
            else:
                image_url, source = (
                    self._analyze_service.extract_best_image_url_with_source(
                        media_info
                    )
                )
            if image_url:
                logger.info("Instagram image extraction source: %s", source)

        if not image_url and detect_platform(job.request.url) == "Instagram":
            image_url = self._analyze_service.extract_instagram_image_from_html(
                self._download_url(job.request.url),
                configured_instagram_cookiefile(),
            )
        if not image_url and detect_platform(job.request.url) == "X/Twitter":
            image_url = self._analyze_service.extract_x_image_from_html_or_metadata(
                self._download_url(job.request.url),
            )
        if not image_url:
            if detect_platform(job.request.url) == "X/Twitter":
                raise RuntimeError("Could not find a downloadable image for this X post.")
            raise RuntimeError(
                INSTAGRAM_PHOTO_UNAVAILABLE_MESSAGE
            )

        output_base = job_dir / self._sanitize_filename(item.formatId)
        saved_path = self._download_image_url(image_url, output_base)
        self._register_file(job, saved_path, "image")

    def _extract_info(self, url: str) -> dict:
        options = {
            "skip_download": True,
        }
        try:
            import yt_dlp

            options = build_ytdlp_options(
                detect_platform(url),
                "metadata",
                options,
            )

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self._download_url(url), download=False)
        except InstagramAuthError as exc:
            raise RuntimeError(f"yt-dlp metadata failed: {self._safe_error(exc)}") from exc
        except Exception as exc:
            raise RuntimeError(f"yt-dlp metadata failed: {self._safe_error(exc)}") from exc
        if not isinstance(info, dict):
            raise RuntimeError("yt-dlp returned invalid metadata")
        return info

    def _format_selector(self, item: SelectedDownloadItem, platform: str = "") -> str:
        if self._is_audio_item(item):
            return "bestaudio/best"

        height_map = {
            "480p": 480,
            "720p": 720,
            "1080p": 1080,
            "2160p": 2160,
        }
        height = height_map.get(self._format_id(item))
        if not height:
            return "best"

        if platform == "Instagram":
            return (
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]/bestvideo+bestaudio/best"
            )

        if shutil.which("ffmpeg"):
            return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
        logger.info("ffmpeg not found. Using single-file format fallback.")
        return f"best[height<={height}]/best"

    def _download_instagram_with_cli(
        self,
        job: DownloadJob,
        item: SelectedDownloadItem,
        job_dir: Path,
        download_url: str,
        output_template: str,
        item_type: str,
    ) -> None:
        cookiefile = configured_instagram_cookiefile()
        cookie_file_exists = bool(cookiefile and Path(cookiefile).is_file())
        if not cookiefile:
            raise RuntimeError(
                "Instagram download auth misconfigured: cookiefile missing from yt-dlp options."
            )

        is_audio_item = self._is_audio_item(item)
        cli_format = self._instagram_cli_format_selector(item)
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--cookies",
            cookiefile,
            "--impersonate",
            "chrome",
            "-f",
            cli_format,
        ]
        if is_audio_item:
            command.extend(["-x", "--audio-format", self._audio_codec(item)])
        command.extend(["-o", output_template, download_url])

        logger.info(
            "Instagram CLI download start: job_id=%s type=%s formatId=%s "
            "cookieFileExists=%s impersonate=chrome outputTemplate=%s",
            job.job_id,
            item_type,
            self._format_id(item),
            cookie_file_exists,
            output_template,
        )

        before = set(job_dir.iterdir())
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Instagram download timed out.") from exc

        if result.returncode != 0:
            error = result.stderr or result.stdout or "Instagram download failed."
            logger.error(
                "Instagram CLI download failed: returnCode=%s stderr=%s",
                result.returncode,
                error[-1500:],
            )
            raise RuntimeError("Instagram CLI download failed.")

        after = set(job_dir.iterdir())
        new_files = [
            path
            for path in after - before
            if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}
        ]
        output_name = self._audio_codec(item) if is_audio_item else self._format_id(item)
        if not new_files:
            new_files = sorted(job_dir.glob(f"{output_name}.*"))
        if is_audio_item:
            new_files = self._validated_audio_outputs(job_dir, item, new_files)
        else:
            new_files = self._validated_media_outputs(new_files)
        for path in new_files:
            self._register_file(job, path, "audio" if is_audio_item else item_type)

    def _instagram_cli_format_selector(self, item: SelectedDownloadItem) -> str:
        if self._is_audio_item(item):
            return "bestaudio/best"
        height_map = {
            "480p": 480,
            "720p": 720,
            "1080p": 1080,
            "2160p": 2160,
        }
        height = height_map.get(self._format_id(item))
        if not height:
            return "best"
        return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"

    def _validated_media_outputs(self, files: list[Path]) -> list[Path]:
        valid_files = [
            path
            for path in files
            if path.is_file()
            and path.stat().st_size >= MIN_IMAGE_BYTES
            and path.suffix.lower() not in {".part", ".ytdl"}
        ]
        if not valid_files:
            raise RuntimeError("Instagram download did not produce a valid media file.")
        return valid_files

    def _configure_audio_options(
        self,
        options: dict,
        item: SelectedDownloadItem,
    ) -> None:
        if not self._ffmpeg_available():
            raise RuntimeError("Audio extraction requires FFmpeg on the server.")

        codec = self._audio_codec(item)
        postprocessor = {
            "key": "FFmpegExtractAudio",
            "preferredcodec": codec,
        }
        if codec == "mp3":
            postprocessor["preferredquality"] = "192"
        options["postprocessors"] = [postprocessor]
        options["keepvideo"] = False

    def _is_audio_item(self, item: SelectedDownloadItem) -> bool:
        return self._item_type(item) == "audio" or self._format_id(item) in {"mp3", "m4a"}

    def _format_id(self, item: SelectedDownloadItem) -> str:
        return (item.formatId or "").strip().lower()

    def _item_type(self, item: SelectedDownloadItem) -> str:
        return (item.type or "").strip().lower()

    def _audio_codec(self, item: SelectedDownloadItem) -> str:
        return "m4a" if self._format_id(item) == "m4a" else "mp3"

    def _validated_audio_outputs(
        self,
        job_dir: Path,
        item: SelectedDownloadItem,
        new_files: list[Path],
    ) -> list[Path]:
        codec = self._audio_codec(item)
        expected_suffix = f".{codec}"
        expected_name = f"{codec}{expected_suffix}"
        candidates = [path for path in new_files if path.is_file()]
        candidates.extend(
            path
            for path in job_dir.glob(f"{codec}.*")
            if path.is_file() and path not in candidates
        )
        converted_files = [
            path
            for path in candidates
            if path.name.lower() == expected_name or path.suffix.lower() == expected_suffix
        ]
        converted_files = [path for path in converted_files if path.stat().st_size > 0]
        if converted_files:
            return converted_files

        wrong_container = [
            path.name
            for path in candidates
            if path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
        ]
        if wrong_container:
            logger.error(
                "Audio conversion produced original media container instead of %s. files=%s",
                expected_name,
                wrong_container,
            )
        raise RuntimeError("Audio conversion failed. FFmpeg may be missing on the server.")

    def _ffmpeg_available(self) -> bool:
        ffmpeg_path = shutil.which("ffmpeg")
        ffprobe_path = shutil.which("ffprobe")
        logger.info(
            "Audio extraction tool availability. ffmpeg=%s ffprobe=%s",
            bool(ffmpeg_path),
            bool(ffprobe_path),
        )
        return bool(ffmpeg_path)

    def _ensure_instagram_download_auth(
        self,
        url: str,
        item: SelectedDownloadItem,
        options: dict,
    ) -> None:
        if detect_platform(url) != "Instagram":
            return
        status = get_instagram_auth_status()
        cookiefile_in_options = bool(options.get("cookiefile"))
        logger.info(
            "Instagram download auth: authMode=%s cookieFileExists=%s "
            "cookieFileLooksValid=%s cookieFileInYdlOpts=%s selectedFormatId=%s",
            status["authMode"],
            status["cookieFileExists"],
            status["cookieFileLooksValid"],
            cookiefile_in_options,
            self._format_id(item),
        )
        if status["authMode"] == "cookiefile" and not cookiefile_in_options:
            raise RuntimeError(
                "Instagram download auth misconfigured: cookiefile missing from yt-dlp options."
            )

    def _progress_hook(self, job: DownloadJob, base_progress: int):
        def hook(data: dict) -> None:
            status = data.get("status")
            if status == "downloading":
                percent = self._progress_percent(data, base_progress)
                self._update_job(
                    job,
                    status="processing",
                    progress=percent,
                    message="Downloading",
                )
            elif status == "finished":
                self._update_job(
                    job,
                    status="processing",
                    progress=max(job.progress, 95),
                    message="Preparing file",
                )

        return hook

    def _progress_percent(self, data: dict, base_progress: int) -> int:
        downloaded = data.get("downloaded_bytes") or 0
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        if total:
            item_progress = min(downloaded / total, 1)
            return min(95, max(base_progress, base_progress + int(item_progress * 80)))
        return min(95, max(base_progress, 25))

    def _register_file(self, job: DownloadJob, path: Path, file_type: str) -> None:
        safe_name = self._sanitize_filename(path.name)
        if safe_name != path.name:
            safe_path = path.with_name(safe_name)
            path.rename(safe_path)
            path = safe_path

        file_id = f"file_{uuid.uuid4().hex[:16]}"
        file = DownloadFile(
            fileId=file_id,
            filename=path.name,
            fileName=path.name,
            type=file_type,
            size=self._human_size(path.stat().st_size),
            downloadUrl=f"/api/file/{file_id}",
        )
        with self._lock:
            self._files[file_id] = path
            job.files.append(file)

    def _update_job(
        self,
        job: DownloadJob,
        status: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0, min(progress, 100))
            if message is not None:
                job.message = message
            if error is not None:
                job.error = error

    def _get_job(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _download_url(self, url: str) -> str:
        if detect_platform(url) == "Instagram":
            return self._analyze_service._clean_instagram_url(url)
        return url

    def _should_try_image_html_fallback(self, url: str, exc: Exception) -> bool:
        platform = detect_platform(url)
        parsed = urlparse(self._download_url(url))
        message = self._safe_error(exc).lower()
        if platform == "Instagram":
            if not parsed.path.startswith("/p/"):
                return False
            return (
                "no video formats found" in message
                or "yt-dlp metadata failed" in message
                or "instagram" in message
            )
        if platform == "X/Twitter":
            return (
                "no video could be found" in message
                or "no video found" in message
                or "yt-dlp metadata failed" in message
                or "twitter" in message
            )
        return False

    def _sanitize_filename(self, value: str) -> str:
        name = Path(value).name
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        name = name.strip().strip(".")
        return name or "apexload_file"

    def _extension_from_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("format", "fm"):
            value = query.get(key, [""])[0].lower()
            if value in {"jpg", "jpeg", "png", "webp"}:
                return value
        suffix = Path(parsed.path).suffix.lower().lstrip(".")
        if suffix in {"jpg", "jpeg", "png", "webp"}:
            return suffix
        return None

    def _download_image_url(self, image_url: str, output_base: Path) -> Path:
        referer = (
            "https://x.com/"
            if "twimg.com" in image_url.lower()
            else "https://www.instagram.com/"
        )
        request = Request(
            image_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": referer,
                "Accept": "image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        with urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise RuntimeError("Could not download image bytes.")
            content_type = response.headers.get("content-type", "")
            url_ext = self._extension_from_url(image_url)
            content_ext = self._extension_from_content_type(content_type)
            if not content_type.lower().startswith("image/") and not url_ext:
                raise RuntimeError("Could not download image bytes.")
            ext = url_ext or content_ext or "jpg"
            output_path = output_base.with_suffix(f".{ext}")
            image_bytes = response.read()
            if len(image_bytes) < MIN_IMAGE_BYTES:
                raise RuntimeError("Could not find a downloadable image for this post.")
            output_path.write_bytes(image_bytes)
            return output_path

    def _extension_from_content_type(self, content_type: str) -> str | None:
        value = content_type.lower().split(";")[0].strip()
        return {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }.get(value)

    def _image_extension(self, item: SelectedDownloadItem) -> str:
        if item.formatId == "png":
            return "png"
        if item.formatId == "webp":
            return "webp"
        return "jpg"

    def _human_size(self, size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"

    def _safe_error(self, exc: Exception) -> str:
        raw_message = str(exc)
        raw_message = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw_message)
        message = " ".join(raw_message.split())
        return message[:240] or "Download failed"

    def _download_error_message(
        self,
        url: str,
        exc: Exception,
        item: SelectedDownloadItem | None = None,
    ) -> str:
        message = self._safe_error(exc)
        if item and self._is_audio_item(item):
            if "requested format is not available" in message.lower():
                return "Audio source is not available for this link."
            if "ffmpeg" in message.lower():
                return "Audio extraction requires ffmpeg on the server."
        if detect_platform(url) == "Instagram" and self._is_instagram_blocked_error(
            message
        ):
            return (
                "Instagram requires a valid server-side session. Please refresh "
                "Instagram cookies from the admin panel."
            )
        if detect_platform(url) == "YouTube Shorts" and (
            "sign in to confirm" in message.lower() or "not a bot" in message.lower()
        ):
            return (
                "YouTube requested sign-in verification. Please try another "
                "link or configure YouTube cookies."
            )
        return f"yt-dlp download failed: {message}"

    def _is_instagram_blocked_error(self, message: str) -> bool:
        text = message.lower()
        return any(
            marker in text
            for marker in (
                "login required",
                "rate-limit",
                "rate limit",
                "requested content is not available",
                "content is not available",
                "cookies",
                "cookie",
                "empty media response",
                "api is not granting access",
                "please wait a few minutes",
                "unable to extract",
                "http error 401",
                "http error 403",
                "checkpoint",
                "challenge",
                "server-side session",
                "cookie file is missing",
                "browser cookies are disabled",
            )
        )


download_service = DownloadService()

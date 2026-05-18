import logging
import re
import shutil
import threading
import uuid
from pathlib import Path
from urllib.request import urlretrieve

from app.models.download_models import (
    DownloadFile,
    DownloadRequest,
    DownloadStartResponse,
    DownloadStatusResponse,
    SelectedDownloadItem,
)
from app.services.ytdlp_analyze_service import YtDlpAnalyzeService
from app.utils.platform_detector import detect_platform

logger = logging.getLogger("apexload.download")

DOWNLOAD_ROOT = Path("storage/downloads")


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
                self._download_item(job, item, job_dir, base_progress)

            self._update_job(
                job,
                status="completed",
                progress=100,
                message="Download completed",
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
        self._update_job(
            job,
            status="processing",
            progress=max(base_progress, 10),
            message=f"Downloading {item.formatId}",
        )
        if item.type == "image" or item.formatId in {"thumbnail", "jpg", "png", "original"}:
            self._download_image_or_thumbnail(job, item, job_dir)
            return

        download_url = self._download_url(job.request.url)
        output_template = str(job_dir / f"{item.formatId}.%(ext)s")
        options = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": output_template,
            "format": self._format_selector(item),
            "progress_hooks": [self._progress_hook(job, base_progress)],
            "restrictfilenames": True,
            "noplaylist": True,
        }

        cookiefile = self._instagram_cookiefile_if_needed(job.request.url)
        if cookiefile:
            options["cookiefile"] = cookiefile

        try:
            import yt_dlp

            before = set(job_dir.iterdir())
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([download_url])
            after = set(job_dir.iterdir())
        except Exception as exc:
            raise RuntimeError(f"yt-dlp download failed: {self._safe_error(exc)}") from exc

        new_files = [path for path in after - before if path.is_file()]
        if not new_files:
            new_files = sorted(job_dir.glob(f"{item.formatId}.*"))
        for path in new_files:
            self._register_file(job, path, item.type)

    def _download_image_or_thumbnail(
        self,
        job: DownloadJob,
        item: SelectedDownloadItem,
        job_dir: Path,
    ) -> None:
        info = self._extract_info(job.request.url)
        media_info = self._analyze_service._select_media_info(info)
        image_url = self._analyze_service._thumbnail(media_info)
        if not image_url:
            raise RuntimeError("Image or thumbnail is not available")

        ext = self._extension_from_url(image_url) or self._image_extension(item)
        output_path = job_dir / f"{self._sanitize_filename(item.formatId)}.{ext}"
        urlretrieve(image_url, output_path)
        self._register_file(job, output_path, "image")

    def _extract_info(self, url: str) -> dict:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        cookiefile = self._instagram_cookiefile_if_needed(url)
        if cookiefile:
            options["cookiefile"] = cookiefile
        try:
            import yt_dlp

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self._download_url(url), download=False)
        except Exception as exc:
            raise RuntimeError(f"yt-dlp metadata failed: {self._safe_error(exc)}") from exc
        if not isinstance(info, dict):
            raise RuntimeError("yt-dlp returned invalid metadata")
        return info

    def _format_selector(self, item: SelectedDownloadItem) -> str:
        if item.type == "audio" or item.formatId == "mp3":
            return "bestaudio/best"

        height_map = {
            "480p": 480,
            "720p": 720,
            "1080p": 1080,
            "2160p": 2160,
        }
        height = height_map.get(item.formatId)
        if not height:
            return "best"

        if shutil.which("ffmpeg"):
            return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
        logger.info("ffmpeg not found. Using single-file format fallback.")
        return f"best[height<={height}]/best"

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

    def _instagram_cookiefile_if_needed(self, url: str) -> str | None:
        if detect_platform(url) != "Instagram":
            return None
        return self._analyze_service._instagram_cookiefile()

    def _download_url(self, url: str) -> str:
        if detect_platform(url) == "Instagram":
            return self._analyze_service._clean_instagram_url(url)
        return url

    def _sanitize_filename(self, value: str) -> str:
        name = Path(value).name
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        name = name.strip().strip(".")
        return name or "apexload_file"

    def _extension_from_url(self, url: str) -> str | None:
        suffix = Path(url.split("?")[0]).suffix.lower().lstrip(".")
        if suffix in {"jpg", "jpeg", "png", "webp"}:
            return suffix
        return None

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
        message = " ".join(str(exc).split())
        return message[:240] or "Download failed"


download_service = DownloadService()

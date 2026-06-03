import logging
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.models.download_models import DownloadFile
from app.models.editor_models import (
    EditorOptions,
    EditorRequest,
    EditorStartResponse,
    EditorStatusResponse,
)
from app.services.download_service import download_service

logger = logging.getLogger("apexload.editor")

EDITOR_ROOT = Path("storage/editor")
EDITOR_ERROR_MESSAGE = "Could not edit this file. Please try another file or option."
SOURCE_FILE_MISSING_MESSAGE = (
    "The original file could not be found. Please download it again and try editing."
)


class EditorJob:
    def __init__(
        self,
        job_id: str,
        operation: str,
        request: EditorRequest,
    ) -> None:
        self.job_id = job_id
        self.operation = operation
        self.request = request
        self.status = "queued"
        self.progress = 0
        self.message = "Editor job created"
        self.error: str | None = None
        self.file: DownloadFile | None = None


class EditorService:
    def __init__(self) -> None:
        self._jobs: dict[str, EditorJob] = {}
        self._lock = threading.Lock()
        EDITOR_ROOT.mkdir(parents=True, exist_ok=True)

    def create_job(self, operation: str, request: EditorRequest) -> EditorStartResponse:
        self._validate_request(operation, request)
        job_id = f"editor_{uuid.uuid4().hex[:12]}"
        job = EditorJob(job_id=job_id, operation=operation, request=request)
        with self._lock:
            self._jobs[job_id] = job
        logger.info("Editor job created. job_id=%s operation=%s", job_id, operation)
        return EditorStartResponse(
            success=True,
            jobId=job_id,
            status="queued",
            message="Editor job created",
        )

    def process_job(self, job_id: str) -> None:
        job = self._get_job(job_id)
        if job is None:
            logger.error("Editor job not found. job_id=%s", job_id)
            return

        job_dir = EDITOR_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        self._update_job(job, status="processing", progress=10, message="Editing file")

        try:
            logger.info(
                "Editor request received. job_id=%s operation=%s fileId=%s "
                "downloadUrl=%s",
                job.job_id,
                job.operation,
                (job.request.fileId or "").strip() or None,
                (job.request.downloadUrl or "").strip() or None,
            )
            source = self._source_path(job.request)
            logger.info(
                "Editor source resolved. job_id=%s source=%s exists=%s size=%s",
                job.job_id,
                source,
                source.is_file(),
                source.stat().st_size if source.is_file() else None,
            )
            self._validate_source(source)
            output = self._output_path(job_dir, source, job.operation, job.request.options)
            command = self._command(job.operation, source, output, job.request.options)
            logger.info(
                "Editor job started. job_id=%s operation=%s source=%s output=%s "
                "ffmpegCommandType=%s",
                job.job_id,
                job.operation,
                source,
                output,
                self._command_type(job.operation),
            )
            self._update_job(job, progress=35, message="Running editor")
            result = self._run_ffmpeg(
                job=job,
                command=command,
                output=output,
                fallback_command=self._fallback_command(
                    job.operation,
                    source,
                    output,
                    job.request.options,
                ),
            )
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError(EDITOR_ERROR_MESSAGE)

            file = download_service.register_external_file(
                output,
                self._output_type(job.operation, output),
            )
            self._update_job(
                job,
                status="completed",
                progress=100,
                message="Edited file is ready",
                file=file,
            )
        except Exception as exc:
            logger.exception("Editor job failed. job_id=%s operation=%s", job_id, job.operation)
            self._update_job(
                job,
                status="failed",
                progress=0,
                message="Edit failed",
                error=self._safe_error(exc),
            )

    def get_status(self, job_id: str) -> EditorStatusResponse:
        job = self._get_job(job_id)
        if job is None:
            return EditorStatusResponse(
                success=False,
                jobId=job_id,
                status="failed",
                progress=0,
                operation="unknown",
                message="Editor job not found",
                error="Editor job not found",
            )
        with self._lock:
            return EditorStatusResponse(
                success=job.status != "failed",
                jobId=job.job_id,
                status=job.status,
                progress=job.progress,
                operation=job.operation,
                message=job.message,
                file=job.file,
                error=job.error,
            )

    def _validate_request(self, operation: str, request: EditorRequest) -> None:
        if operation not in {"trim", "extract-audio", "mute", "compress", "convert"}:
            raise ValueError("Unsupported editor operation")
        if not (request.fileId or request.downloadUrl):
            raise ValueError("Missing fileId")
        if operation == "trim":
            start = request.options.startTime
            end = request.options.endTime
            if start is None or end is None or start < 0 or end <= start:
                raise ValueError("Invalid trim times")
        if operation == "convert":
            fmt = (request.options.format or "mp4").lower()
            if fmt not in {"mp4", "mp3", "webm"}:
                raise ValueError("Unsupported convert format")

    def _source_path(self, request: EditorRequest) -> Path:
        file_id = (request.fileId or "").strip()
        if not file_id and request.downloadUrl:
            file_id = self._file_id_from_url(request.downloadUrl)
        logger.info(
            "Editor source lookup. receivedFileId=%s receivedDownloadUrl=%s "
            "resolvedFileId=%s",
            (request.fileId or "").strip() or None,
            (request.downloadUrl or "").strip() or None,
            file_id or None,
        )
        if not file_id:
            raise RuntimeError("Missing fileId")
        path = download_service.get_file_path(file_id)
        if path is None:
            logger.info(
                "Editor source missing. resolvedFileId=%s sourceFileExists=False",
                file_id,
            )
            raise RuntimeError("Source file does not exist")
        return path

    def _file_id_from_url(self, value: str) -> str:
        parsed = urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[-2] == "file":
            return parts[-1]
        if value.startswith("/api/file/"):
            return value.rsplit("/", 1)[-1]
        return ""

    def _validate_source(self, source: Path) -> None:
        if not source.is_file():
            raise RuntimeError("Source file does not exist")
        if source.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".mp3", ".m4a"}:
            raise RuntimeError("Unsupported file type")
        if not shutil.which("ffmpeg"):
            raise RuntimeError("FFmpeg is not available on the server")

    def _output_path(
        self,
        job_dir: Path,
        source: Path,
        operation: str,
        options: EditorOptions,
    ) -> Path:
        base = self._safe_stem(source.stem)
        suffix = {
            "trim": ".mp4",
            "extract-audio": ".mp3",
            "mute": ".mp4",
            "compress": ".mp4",
            "convert": f".{(options.format or 'mp4').lower()}",
        }[operation]
        return job_dir / f"{base}_{operation.replace('-', '_')}{suffix}"

    def _command(
        self,
        operation: str,
        source: Path,
        output: Path,
        options: EditorOptions,
    ) -> list[str]:
        if operation == "trim":
            return [
                "ffmpeg",
                "-y",
                "-ss",
                self._time(options.startTime),
                "-to",
                self._time(options.endTime),
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output),
            ]
        if operation == "extract-audio":
            return [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(output),
            ]
        if operation == "mute":
            return [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-an",
                "-c:v",
                "copy",
                str(output),
            ]
        if operation == "compress":
            crf = {"low": "32", "medium": "28", "high": "23"}.get(
                (options.quality or "medium").lower(),
                "28",
            )
            return [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vcodec",
                "libx264",
                "-crf",
                crf,
                "-preset",
                "veryfast",
                "-acodec",
                "aac",
                "-movflags",
                "+faststart",
                str(output),
            ]
        fmt = (options.format or "mp4").lower()
        if fmt == "mp3":
            return self._command("extract-audio", source, output, options)
        if fmt == "webm":
            return [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vcodec",
                "libvpx-vp9",
                "-acodec",
                "libopus",
                str(output),
            ]
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vcodec",
            "libx264",
            "-acodec",
            "aac",
            "-movflags",
            "+faststart",
            str(output),
        ]

    def _fallback_command(
        self,
        operation: str,
        source: Path,
        output: Path,
        options: EditorOptions,
    ) -> list[str] | None:
        if operation != "mute":
            return None
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(output),
        ]

    def _run_ffmpeg(
        self,
        job: EditorJob,
        command: list[str],
        output: Path,
        fallback_command: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        logger.info(
            "Editor ffmpeg finished. job_id=%s operation=%s exitCode=%s",
            job.job_id,
            job.operation,
            result.returncode,
        )
        if result.returncode == 0:
            return result
        logger.error(
            "Editor ffmpeg stderr. job_id=%s operation=%s stderr=%s",
            job.job_id,
            job.operation,
            self._safe_stderr(result.stderr),
        )
        if fallback_command is None:
            raise RuntimeError(EDITOR_ERROR_MESSAGE)
        logger.info(
            "Editor ffmpeg fallback started. job_id=%s operation=%s output=%s",
            job.job_id,
            job.operation,
            output,
        )
        fallback = subprocess.run(
            fallback_command,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        logger.info(
            "Editor ffmpeg fallback finished. job_id=%s operation=%s exitCode=%s",
            job.job_id,
            job.operation,
            fallback.returncode,
        )
        if fallback.returncode != 0:
            logger.error(
                "Editor ffmpeg fallback stderr. job_id=%s operation=%s stderr=%s",
                job.job_id,
                job.operation,
                self._safe_stderr(fallback.stderr),
            )
            raise RuntimeError(EDITOR_ERROR_MESSAGE)
        return fallback

    def _command_type(self, operation: str) -> str:
        return {
            "trim": "trim_transcode_h264_aac",
            "extract-audio": "extract_mp3",
            "mute": "mute_copy_with_transcode_fallback",
            "compress": "compress_h264_aac",
            "convert": "convert",
        }.get(operation, operation)

    def _output_type(self, operation: str, output: Path) -> str:
        if operation == "extract-audio" or output.suffix.lower() in {".mp3", ".m4a"}:
            return "audio"
        return "video"

    def _time(self, value: float | None) -> str:
        return f"{max(value or 0, 0):.3f}"

    def _safe_stem(self, value: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip("._ ")
        return cleaned or "apexload_edit"

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc) or EDITOR_ERROR_MESSAGE
        if message in {
            "Missing fileId",
            "Unsupported file type",
            "Invalid trim times",
            "Unsupported convert format",
        }:
            return message
        if message == "Source file does not exist":
            return SOURCE_FILE_MISSING_MESSAGE
        return EDITOR_ERROR_MESSAGE

    def _safe_stderr(self, value: str | None) -> str:
        if not value:
            return ""
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        compact = " | ".join(lines[-8:])
        compact = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", compact)
        return compact[:1500]

    def _update_job(
        self,
        job: EditorJob,
        status: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        file: DownloadFile | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0, min(progress, 100))
            if message is not None:
                job.message = message
            if file is not None:
                job.file = file
            if error is not None:
                job.error = error

    def _get_job(self, job_id: str) -> EditorJob | None:
        with self._lock:
            return self._jobs.get(job_id)


editor_service = EditorService()

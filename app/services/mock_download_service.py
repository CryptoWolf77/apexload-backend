from time import time

from app.models.download_models import (
    DownloadFile,
    DownloadRequest,
    DownloadStartResponse,
    DownloadStatusResponse,
)


class MockDownloadService:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloadRequest] = {}

    def create_job(self, request: DownloadRequest) -> DownloadStartResponse:
        job_id = f"job_demo_{int(time() * 1000)}"
        self._jobs[job_id] = request
        return DownloadStartResponse(
            success=True,
            jobId=job_id,
            status="queued",
            message="Demo download job created",
        )

    def get_status(self, job_id: str) -> DownloadStatusResponse:
        return DownloadStatusResponse(
            success=True,
            jobId=job_id,
            status="completed",
            progress=100,
            message="Demo files are ready",
            files=self._demo_files(),
        )

    def _demo_files(self) -> list[DownloadFile]:
        return [
            DownloadFile(
                fileId="demo_video_1",
                fileName="apexload_demo_video.mp4",
                type="video",
                size="24 MB",
                downloadUrl="/api/file/demo_video_1",
            ),
            DownloadFile(
                fileId="demo_audio_1",
                fileName="apexload_demo_audio.mp3",
                type="audio",
                size="4 MB",
                downloadUrl="/api/file/demo_audio_1",
            ),
            DownloadFile(
                fileId="demo_thumb_1",
                fileName="apexload_thumbnail.jpg",
                type="image",
                size="860 KB",
                downloadUrl="/api/file/demo_thumb_1",
            ),
        ]


from pydantic import BaseModel, Field

from app.models.download_models import DownloadFile


class EditorOptions(BaseModel):
    startTime: float | None = None
    endTime: float | None = None
    format: str | None = None
    quality: str | None = None
    mute: bool | None = None


class EditorRequest(BaseModel):
    fileId: str | None = None
    downloadUrl: str | None = None
    options: EditorOptions = Field(default_factory=EditorOptions)


class EditorStartResponse(BaseModel):
    success: bool
    jobId: str
    status: str
    message: str


class EditorStatusResponse(BaseModel):
    success: bool
    jobId: str
    status: str
    progress: int
    operation: str
    message: str
    file: DownloadFile | None = None
    error: str | None = None

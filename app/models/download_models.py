from pydantic import BaseModel, Field


class SelectedDownloadItem(BaseModel):
    formatId: str
    type: str


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=1)
    selectedItems: list[SelectedDownloadItem] = Field(default_factory=list)
    premium: bool = False
    noWatermark: bool = False


class DownloadStartResponse(BaseModel):
    success: bool
    jobId: str
    status: str
    message: str


class DownloadFile(BaseModel):
    fileId: str
    fileName: str
    type: str
    size: str
    downloadUrl: str


class DownloadStatusResponse(BaseModel):
    success: bool
    jobId: str
    status: str
    progress: int
    message: str
    files: list[DownloadFile]


class FileEndpointResponse(BaseModel):
    success: bool
    fileId: str
    message: str


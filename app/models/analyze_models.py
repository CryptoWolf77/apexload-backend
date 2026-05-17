from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    url: str = Field(..., min_length=1, examples=["https://www.instagram.com/reel/example"])


class FormatOption(BaseModel):
    id: str
    label: str
    type: str
    quality: str
    size: str | None = None
    premium: bool
    available: bool
    unavailableReason: str | None = None


class AnalyzeResponse(BaseModel):
    success: bool
    platform: str
    mediaType: str
    title: str
    thumbnail: str
    duration: str | None = None
    formats: list[FormatOption]


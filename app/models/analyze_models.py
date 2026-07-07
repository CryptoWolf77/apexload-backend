from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    url: str = Field(
        ...,
        min_length=1,
        examples=["https://www.instagram.com/reel/example"],
    )


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
    source: str | None = None
    platform: str | None = None
    mediaType: str | None = None
    title: str | None = None
    thumbnail: str | None = None
    duration: str | None = None
    formats: list[FormatOption] = Field(default_factory=list)
    error: str | None = None
    code: str | None = None
    message: str | None = None

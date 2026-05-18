from app.models.analyze_models import AnalyzeResponse, FormatOption
from app.utils.platform_detector import detect_media_type, detect_platform


class MockAnalyzeService:
    def analyze(self, url: str) -> AnalyzeResponse:
        platform = detect_platform(url)
        media_type = detect_media_type(url)

        if media_type == "image":
            return AnalyzeResponse(
                success=True,
                source="mock_fallback",
                platform=platform,
                mediaType="image",
                title=self._image_title(platform),
                thumbnail="https://picsum.photos/600/600",
                duration=None,
                formats=self._image_formats(),
            )

        return AnalyzeResponse(
            success=True,
            source="mock_fallback",
            platform=platform,
            mediaType="video",
            title="Amazing travel sunset video",
            thumbnail="https://picsum.photos/600/400",
            duration="00:32",
            formats=self._video_formats(),
        )

    def _image_title(self, platform: str) -> str:
        if platform == "Pinterest":
            return "Pinterest design inspiration"
        if platform == "Snapchat":
            return "Snapchat image story"
        return "Creative Instagram photo post"

    def _video_formats(self) -> list[FormatOption]:
        return [
            FormatOption(
                id="480p",
                label="MP4 480p",
                type="video",
                quality="480p",
                size="12 MB",
                premium=False,
                available=True,
            ),
            FormatOption(
                id="720p",
                label="MP4 720p",
                type="video",
                quality="720p",
                size="24 MB",
                premium=False,
                available=True,
            ),
            FormatOption(
                id="1080p",
                label="MP4 1080p",
                type="video",
                quality="1080p",
                size="42 MB",
                premium=True,
                available=True,
            ),
            FormatOption(
                id="2160p",
                label="MP4 2160p / 4K",
                type="video",
                quality="2160p",
                size=None,
                premium=True,
                available=False,
                unavailableReason="Not available on this clip",
            ),
            FormatOption(
                id="mp3",
                label="MP3 Audio",
                type="audio",
                quality="audio",
                size="4 MB",
                premium=True,
                available=True,
            ),
            FormatOption(
                id="thumbnail",
                label="Thumbnail JPG",
                type="image",
                quality="thumbnail",
                size="860 KB",
                premium=False,
                available=True,
            ),
        ]

    def _image_formats(self) -> list[FormatOption]:
        return [
            FormatOption(
                id="original",
                label="Original Image",
                type="image",
                quality="original",
                size="2.4 MB",
                premium=False,
                available=True,
            ),
            FormatOption(
                id="jpg",
                label="JPG Image",
                type="image",
                quality="jpg",
                size="1.8 MB",
                premium=False,
                available=True,
            ),
            FormatOption(
                id="png",
                label="PNG Image",
                type="image",
                quality="png",
                size=None,
                premium=True,
                available=False,
                unavailableReason="Not available for this image",
            ),
            FormatOption(
                id="high_quality",
                label="High Quality Image",
                type="image",
                quality="high_quality",
                size="3.6 MB",
                premium=True,
                available=True,
            ),
            FormatOption(
                id="compressed",
                label="Compressed Image",
                type="image",
                quality="compressed",
                size="620 KB",
                premium=False,
                available=True,
            ),
        ]

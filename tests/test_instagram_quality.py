import unittest

from app.models.download_models import SelectedDownloadItem
from app.services.download_service import DownloadService
from app.services.ytdlp_analyze_service import YtDlpAnalyzeService


class InstagramAnalyzeResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = YtDlpAnalyzeService()

    def test_portrait_1080x1920_is_1080p_not_4k(self) -> None:
        availability = self._availability(1080, 1920)

        self.assertTrue(availability["1080p"])
        self.assertFalse(availability["2160p"])

    def test_landscape_1920x1080_is_1080p(self) -> None:
        availability = self._availability(1920, 1080)

        self.assertTrue(availability["1080p"])

    def test_portrait_and_landscape_720_sources_are_720p(self) -> None:
        for width, height in ((720, 1280), (1280, 720)):
            with self.subTest(width=width, height=height):
                availability = self._availability(width, height)
                self.assertTrue(availability["720p"])
                self.assertFalse(availability["1080p"])

    def test_540x960_does_not_advertise_720p_or_1080p(self) -> None:
        availability = self._availability(540, 960)

        self.assertFalse(availability["720p"])
        self.assertFalse(availability["1080p"])

    def test_root_dimensions_are_used_when_formats_are_incomplete(self) -> None:
        formats = self.service._video_formats(
            {"width": 1080, "height": 1920, "formats": []},
            "",
            "Instagram",
        )
        availability = {item.id: item.available for item in formats}

        self.assertTrue(availability["1080p"])
        self.assertFalse(availability["2160p"])

    def test_missing_dimension_uses_available_dimension(self) -> None:
        self.assertEqual(
            self.service._effective_video_resolution({"width": 720}),
            720,
        )
        self.assertEqual(
            self.service._effective_video_resolution({"height": 1080}),
            1080,
        )

    def test_audio_only_and_invalid_formats_are_ignored(self) -> None:
        resolutions = self.service._available_resolutions(
            {
                "formats": [
                    {
                        "format_id": "audio",
                        "width": 2160,
                        "height": 3840,
                        "vcodec": "none",
                    },
                    {"format_id": "invalid", "width": 0, "height": -1},
                ]
            }
        )

        self.assertEqual(resolutions, set())

    def _availability(self, width: int, height: int) -> dict[str, bool]:
        formats = self.service._video_formats(
            {
                "formats": [
                    {
                        "format_id": "source",
                        "width": width,
                        "height": height,
                        "vcodec": "h264",
                        "ext": "mp4",
                    }
                ]
            },
            "",
            "Instagram",
        )
        return {item.id: item.available for item in formats}


class InstagramDownloadSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DownloadService()

    def test_1080p_uses_short_edge_sort_not_height_filter(self) -> None:
        item = SelectedDownloadItem(formatId="1080p", type="video")

        command, selector, sort_expression = self.service._instagram_cli_command(
            "instagram_cookies.txt",
            item,
            "output.%(ext)s",
            "https://www.instagram.com/reel/example/",
        )

        self.assertEqual(selector, "bv*+ba/b")
        self.assertEqual(sort_expression, "res:1080,fps,br")
        self.assertEqual(self.service._format_selector(item, "Instagram"), "bv*+ba/b")
        self.assertNotIn("height<=1080", " ".join(command))
        self.assertIn("--merge-output-format", command)
        self.assertIn("--remux-video", command)

    def test_each_requested_quality_uses_orientation_safe_sorting(self) -> None:
        for format_id, target in (
            ("480p", 480),
            ("720p", 720),
            ("1080p", 1080),
            ("2160p", 2160),
        ):
            with self.subTest(format_id=format_id):
                item = SelectedDownloadItem(formatId=format_id, type="video")
                sort_expression = self.service._instagram_cli_sort_expression(item)
                self.assertEqual(sort_expression, f"res:{target},fps,br")

    def test_best_video_has_no_resolution_ceiling(self) -> None:
        item = SelectedDownloadItem(formatId="best", type="video")
        command, selector, sort_expression = self.service._instagram_cli_command(
            "instagram_cookies.txt",
            item,
            "output.%(ext)s",
            "https://www.instagram.com/reel/example/",
        )

        self.assertEqual(selector, "bv*+ba/b")
        self.assertIsNone(sort_expression)
        self.assertNotIn("-S", command)

    def test_audio_cli_behavior_is_unchanged(self) -> None:
        item = SelectedDownloadItem(formatId="mp3", type="audio")
        command, selector, sort_expression = self.service._instagram_cli_command(
            "instagram_cookies.txt",
            item,
            "output.%(ext)s",
            "https://www.instagram.com/reel/example/",
        )

        self.assertEqual(selector, "bestaudio/best")
        self.assertIsNone(sort_expression)
        self.assertIn("-x", command)
        self.assertIn("--audio-format", command)
        self.assertNotIn("--remux-video", command)
        self.assertNotIn("-S", command)

    def test_selected_format_diagnostics_capture_portrait_dimensions(self) -> None:
        format_id, width, height = self.service._instagram_selected_format_details(
            "noise\nAPEXLOAD_SELECTED_FORMAT=123+456|1080|1920\n"
        )

        self.assertEqual(format_id, "123+456")
        self.assertEqual(width, 1080)
        self.assertEqual(height, 1920)
        self.assertEqual(self.service._effective_resolution(width, height), 1080)


if __name__ == "__main__":
    unittest.main()

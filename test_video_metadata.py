import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import video_metadata


class VideoMetadataTests(unittest.TestCase):
    def test_uses_stream_time_base_and_applies_display_rotation(self):
        probe_result = SimpleNamespace(
            stdout=json.dumps({
                "streams": [{
                    "width": 1920,
                    "height": 1080,
                    "duration": "N/A",
                    "duration_ts": "1500",
                    "time_base": "1/25",
                    "side_data_list": [{"rotation": 90}],
                }],
                "format": {"duration": "0"},
            })
        )
        with (
            patch.object(video_metadata.shutil, "which", return_value="/ffprobe"),
            patch.object(video_metadata.subprocess, "run", return_value=probe_result),
        ):
            metadata = video_metadata._probe_ffmpeg("episode.mp4")

        self.assertEqual(
            metadata,
            {"duration": 60, "width": 1080, "height": 1920},
        )

    def test_uses_container_duration_when_video_stream_has_no_duration(self):
        probe_result = SimpleNamespace(
            stdout=json.dumps({
                "streams": [{
                    "width": 1920,
                    "height": 1080,
                    "duration": "0",
                    "duration_ts": "N/A",
                }],
                "format": {"duration": "57.2"},
            })
        )
        with (
            patch.object(video_metadata.shutil, "which", return_value="/ffprobe"),
            patch.object(video_metadata.subprocess, "run", return_value=probe_result),
        ):
            metadata = video_metadata._probe_ffmpeg("episode.mp4")

        self.assertEqual(
            metadata,
            {"duration": 57, "width": 1920, "height": 1080},
        )

    def test_mp4_parser_fills_probe_duration_missing(self):
        with (
            patch.object(
                video_metadata, "_probe_ffmpeg",
                return_value={"duration": 0, "width": 1920, "height": 1080},
            ),
            patch.object(
                video_metadata, "_mp4_metadata",
                return_value={"duration": 1421, "width": 1920, "height": 1080},
            ),
        ):
            metadata = video_metadata.get_video_metadata("episode.mp4")

        self.assertEqual(metadata, {
            "duration": 1421,
            "width": 1920,
            "height": 1080,
        })


if __name__ == "__main__":
    unittest.main()
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import server


class VideoIdExtractionTests(unittest.TestCase):
    def test_extracts_supported_youtube_url_forms(self):
        cases = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=10": (
                "dQw4w9WgXcQ"
            ),
            "https://youtu.be/dQw4w9WgXcQ?si=abc": "dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/v/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://youtube.com/live/dQw4w9WgXcQ?feature=share": "dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "dQw4w9WgXcQ": "dQw4w9WgXcQ",
        }

        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(server._extract_video_id(value), expected)

    def test_rejects_invalid_video_id(self):
        cases = [
            "https://example.com/not-youtube",
            "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?notv=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?preview=dQw4w9WgXcQ",
            "https://youtu.be/not-valid",
        ]

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    server._extract_video_id(value)


class TranscriptFormattingTests(unittest.TestCase):
    def test_formats_transcript_without_timestamps_and_skips_blank_entries(self):
        entries = [
            {"text": "  first line  ", "start": 1.2, "duration": 1.0},
            {"text": "   ", "start": 2.2, "duration": 1.0},
            {"text": "second line", "start": 65.9, "duration": 1.0},
        ]

        self.assertEqual(
            server._format_transcript(entries, include_timestamps=False),
            "first line\nsecond line",
        )

    def test_formats_transcript_with_minute_second_timestamps(self):
        entries = [
            {"text": "intro", "start": 5.9, "duration": 1.0},
            {"text": "chapter", "start": 65.9, "duration": 1.0},
        ]

        self.assertEqual(
            server._format_transcript(entries, include_timestamps=True),
            "[00:05] intro\n[01:05] chapter",
        )

    def test_filters_entries_by_open_time_ranges(self):
        entries = [
            {"text": "zero", "start": 0.0, "duration": 10.0},
            {"text": "ten", "start": 10.0, "duration": 5.0},
            {"text": "fifteen", "start": 15.0, "duration": 5.0},
            {"text": "twenty", "start": 20.0, "duration": 5.0},
        ]

        self.assertEqual(
            [
                entry["text"]
                for entry in server._filter_entries_by_range(entries, 5, 10)
            ],
            ["zero"],
        )
        self.assertEqual(
            [
                entry["text"]
                for entry in server._filter_entries_by_range(entries, 10, 15)
            ],
            ["ten"],
        )
        self.assertEqual(
            [
                entry["text"]
                for entry in server._filter_entries_by_range(entries, 12, None)
            ],
            ["ten", "fifteen", "twenty"],
        )
        self.assertEqual(
            [
                entry["text"]
                for entry in server._filter_entries_by_range(entries, None, 10)
            ],
            ["zero"],
        )


class OutputFormattingTests(unittest.TestCase):
    def test_build_output_uses_markdown_metadata_without_frontmatter(self):
        metadata = {
            "title": 'A "quoted" title\nnext line',
            "author": 'Creator "Name"',
            "upload_date": "20240501",
            "duration_seconds": 65,
            "description": "Short description",
        }
        transcript_info = {"language": "en", "source": "manual"}

        output = server._build_output(
            metadata,
            "Transcript body",
            transcript_info,
            "dQw4w9WgXcQ",
            cached=True,
            cached_at="2026-05-27",
        )

        self.assertFalse(output.startswith("---\n"))
        self.assertIn('# A "quoted" title next line', output)
        self.assertIn('- Author: Creator "Name"', output)
        self.assertIn("- Upload date: 2024-05-01", output)
        self.assertIn("- Duration: 1m5s", output)
        self.assertIn("- Cached: true", output)
        self.assertIn("- Cached at: 2026-05-27", output)
        self.assertIn("## Description\n\nShort description", output)
        self.assertIn("## Transcript\n\nTranscript body", output)

    def test_build_limited_output_truncates_on_entry_boundary(self):
        metadata = {"title": "Title", "author": "Author"}
        transcript_info = {"language": "en", "source": "manual"}
        entries = [
            {"text": "short line", "start": 0.0, "duration": 1.0},
            {"text": "x" * 1000, "start": 1.0, "duration": 1.0},
            {"text": "third line", "start": 2.0, "duration": 1.0},
        ]

        output = server._build_limited_output(
            metadata,
            entries,
            transcript_info,
            "dQw4w9WgXcQ",
            include_timestamps=False,
            max_chars=400,
        )

        self.assertLessEqual(len(output), 400)
        self.assertIn("short line", output)
        self.assertNotIn("x" * 80, output)
        self.assertIn("## Continuation", output)
        self.assertIn("- next_start_seconds: 1", output)

    def test_build_limited_output_keeps_end_seconds_in_continuation(self):
        metadata = {"title": "Title", "author": "Author"}
        transcript_info = {"language": "en", "source": "manual"}
        entries = [
            {"text": "short line", "start": 10.0, "duration": 1.0},
            {"text": "x" * 80, "start": 11.0, "duration": 1.0},
        ]

        output = server._build_limited_output(
            metadata,
            entries,
            transcript_info,
            "dQw4w9WgXcQ",
            include_timestamps=False,
            start_seconds=10.0,
            end_seconds=20.0,
            max_chars=260,
        )

        self.assertIn("- Transcript range: 10s-20s", output)
        self.assertIn("end_seconds=20", output)
        self.assertIn("max_chars=260", output)

    def test_build_limited_output_reports_empty_range(self):
        output = server._build_limited_output(
            {"title": "Title", "author": "Author"},
            [],
            {"language": "en", "source": "manual"},
            "dQw4w9WgXcQ",
            include_timestamps=False,
            start_seconds=30.0,
            end_seconds=40.0,
        )

        self.assertIn("- Transcript range: 30s-40s", output)
        self.assertIn("No transcript entries found for the requested range.", output)


class CacheTests(unittest.TestCase):
    def test_save_and_load_cache_round_trip(self):
        transcript_info = {
            "entries": [{"text": "hello", "start": 0.0, "duration": 1.0}],
            "language": "en",
            "source": "manual",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(server.os.environ, {"CACHE_DIR": tmpdir}, clear=False):
                server._save_cache("dQw4w9WgXcQ", ["en"], transcript_info)
                loaded = server._load_cache("dQw4w9WgXcQ", ["en"])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["entries"], transcript_info["entries"])
        self.assertEqual(loaded["language"], "en")
        self.assertEqual(loaded["source"], "manual")
        self.assertEqual(loaded["ttl_days"], server.CACHE_TTL_DAYS)

    def test_load_cache_returns_none_for_expired_entry(self):
        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 5, 27)

        payload = {
            "entries": [{"text": "old", "start": 0.0, "duration": 1.0}],
            "language": "en",
            "source": "manual",
            "cached_at": "2025-01-01",
            "ttl_days": server.CACHE_TTL_DAYS,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "dQw4w9WgXcQ_en.json"
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                patch.dict(server.os.environ, {"CACHE_DIR": tmpdir}, clear=False),
                patch.object(server, "date", FakeDate),
            ):
                loaded = server._load_cache("dQw4w9WgXcQ", ["en"])

        self.assertIsNone(loaded)

    def test_load_cache_returns_none_for_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "dQw4w9WgXcQ_en.json"
            cache_path.write_text("{not json", encoding="utf-8")
            with patch.dict(server.os.environ, {"CACHE_DIR": tmpdir}, clear=False):
                loaded = server._load_cache("dQw4w9WgXcQ", ["en"])

        self.assertIsNone(loaded)


class MetadataTests(unittest.TestCase):
    @staticmethod
    def completed(returncode=0, stdout="", stderr=""):
        return type(
            "Completed",
            (),
            {
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
        )()

    def test_get_metadata_falls_back_when_ytdlp_is_missing(self):
        with patch.object(server.shutil, "which", return_value=None):
            metadata = server._get_metadata("dQw4w9WgXcQ")

        self.assertEqual(metadata["title"], "Unknown")
        self.assertEqual(metadata["author"], "Unknown")
        self.assertEqual(metadata["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(metadata["metadata_error"]["type"], "yt_dlp_not_found")

    def test_get_metadata_uses_ytdlp_json_and_truncates_description_by_default(self):
        completed = self.completed(
            stdout=json.dumps(
                {
                    "title": "Title",
                    "uploader": "Uploader",
                    "channel": "Channel",
                    "channel_url": "https://youtube.com/@channel",
                    "upload_date": "20240501",
                    "duration": 123,
                    "description": "x" * 600,
                    "view_count": 42,
                }
            ),
        )

        with (
            patch.object(server.shutil, "which", return_value="/usr/bin/yt-dlp"),
            patch.object(server.subprocess, "run", return_value=completed),
        ):
            metadata = server._get_metadata("dQw4w9WgXcQ")

        self.assertEqual(metadata["title"], "Title")
        self.assertEqual(metadata["author"], "Uploader")
        self.assertEqual(metadata["channel_url"], "https://youtube.com/@channel")
        self.assertEqual(metadata["upload_date"], "20240501")
        self.assertEqual(metadata["duration_seconds"], 123)
        self.assertEqual(len(metadata["description"]), 500)
        self.assertEqual(metadata["view_count"], 42)
        self.assertEqual(metadata["video_id"], "dQw4w9WgXcQ")

    def test_get_metadata_reports_ytdlp_nonzero_exit(self):
        completed = self.completed(
            returncode=1,
            stderr="ERROR: Video unavailable",
        )

        with (
            patch.object(server.shutil, "which", return_value="/usr/bin/yt-dlp"),
            patch.object(server.subprocess, "run", return_value=completed),
        ):
            metadata = server._get_metadata("dQw4w9WgXcQ")

        self.assertEqual(metadata["title"], "Unknown")
        self.assertEqual(metadata["metadata_error"]["type"], "yt_dlp_failed")
        self.assertEqual(metadata["metadata_error"]["returncode"], 1)
        self.assertIn("Video unavailable", metadata["metadata_error"]["stderr"])

    def test_get_metadata_reports_invalid_ytdlp_json(self):
        completed = self.completed(stdout="{bad json")

        with (
            patch.object(server.shutil, "which", return_value="/usr/bin/yt-dlp"),
            patch.object(server.subprocess, "run", return_value=completed),
        ):
            metadata = server._get_metadata("dQw4w9WgXcQ")

        self.assertEqual(metadata["metadata_error"]["type"], "yt_dlp_invalid_json")
        self.assertIn("{bad json", metadata["metadata_error"]["stdout"])

    def test_get_metadata_reports_ytdlp_timeout(self):
        timeout = subprocess.TimeoutExpired(["yt-dlp"], timeout=15)

        with (
            patch.object(server.shutil, "which", return_value="/usr/bin/yt-dlp"),
            patch.object(server.subprocess, "run", side_effect=timeout),
        ):
            metadata = server._get_metadata("dQw4w9WgXcQ")

        self.assertEqual(metadata["metadata_error"]["type"], "yt_dlp_timeout")
        self.assertEqual(metadata["metadata_error"]["timeout_seconds"], 15)


class ToolTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def cached_entries(entries):
        return {
            "entries": entries,
            "language": "en",
            "source": "manual",
            "cached_at": "2026-05-27",
        }

    async def test_youtube_get_transcript_uses_cache_without_fetching_transcript(self):
        cached = {
            "entries": [{"text": "cached transcript", "start": 0.0, "duration": 1.0}],
            "language": "en",
            "source": "manual",
            "cached_at": "2026-05-27",
        }

        with (
            patch.object(server, "_load_cache", return_value=cached),
            patch.object(server, "_get_transcript") as get_transcript,
            patch.object(
                server,
                "_get_metadata",
                return_value={"title": "Title", "author": "Author"},
            ),
        ):
            output = await server.youtube_get_transcript("dQw4w9WgXcQ")

        get_transcript.assert_not_called()
        self.assertIn("- Cached: true", output)
        self.assertIn("cached transcript", output)

    async def test_youtube_get_transcript_filters_cached_entries_by_range(self):
        cached = self.cached_entries(
            [
                {"text": "before", "start": 0.0, "duration": 10.0},
                {"text": "inside", "start": 10.0, "duration": 5.0},
                {"text": "overlap", "start": 18.0, "duration": 5.0},
                {"text": "after", "start": 20.0, "duration": 5.0},
            ]
        )

        with patch.object(server, "_load_cache", return_value=cached):
            output = await server.youtube_get_transcript(
                "dQw4w9WgXcQ",
                include_metadata=False,
                start_seconds=10.0,
                end_seconds=20.0,
            )

        self.assertIn("- Transcript range: 10s-20s", output)
        self.assertNotIn("before", output)
        self.assertIn("inside", output)
        self.assertIn("overlap", output)
        self.assertNotIn("after", output)

    async def test_youtube_get_transcript_rejects_invalid_range(self):
        output = await server.youtube_get_transcript(
            "dQw4w9WgXcQ",
            start_seconds=20.0,
            end_seconds=10.0,
        )

        self.assertIn("Error: start_seconds must be less than end_seconds.", output)

    async def test_youtube_get_transcript_rejects_out_of_bounds_max_chars(self):
        output = await server.youtube_get_transcript("dQw4w9WgXcQ", max_chars=9_999)

        self.assertIn("Error: max_chars must be between 10000 and 200000.", output)

    async def test_youtube_get_transcript_returns_readable_error_on_fetch_failure(self):
        with (
            patch.object(server, "_load_cache", return_value=None),
            patch.object(
                server,
                "_get_transcript",
                side_effect=RuntimeError("no caps"),
            ),
        ):
            output = await server.youtube_get_transcript("dQw4w9WgXcQ")

        self.assertIn("Error: Could not retrieve transcript", output)
        self.assertIn("no caps", output)

    async def test_youtube_get_video_info_includes_metadata_error(self):
        metadata = {
            "title": "Unknown",
            "author": "Unknown",
            "video_id": "dQw4w9WgXcQ",
            "metadata_error": {
                "type": "yt_dlp_failed",
                "returncode": 1,
                "stderr": "ERROR",
            },
        }

        with patch.object(server, "_get_metadata", return_value=metadata):
            output = await server.youtube_get_video_info("dQw4w9WgXcQ")

        parsed = json.loads(output)
        self.assertEqual(parsed["metadata_error"]["type"], "yt_dlp_failed")


if __name__ == "__main__":
    unittest.main()

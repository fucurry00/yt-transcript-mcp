import inspect
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
        self.assertIn("打ち切りました", output)
        self.assertNotIn("## Continuation", output)

    def test_build_limited_output_returns_full_text_when_within_limit(self):
        metadata = {"title": "Title", "author": "Author"}
        transcript_info = {"language": "en", "source": "manual"}
        entries = [
            {"text": "first line", "start": 0.0, "duration": 1.0},
            {"text": "second line", "start": 1.0, "duration": 1.0},
        ]

        output = server._build_limited_output(
            metadata,
            entries,
            transcript_info,
            "dQw4w9WgXcQ",
            include_timestamps=False,
        )

        self.assertIn("first line\nsecond line", output)
        self.assertNotIn("打ち切りました", output)

    def test_build_limited_output_reports_empty_entries(self):
        output = server._build_limited_output(
            {"title": "Title", "author": "Author"},
            [],
            {"language": "en", "source": "manual"},
            "dQw4w9WgXcQ",
            include_timestamps=False,
        )

        self.assertIn("No transcript entries found.", output)

    def test_build_limited_output_respects_limit_when_first_entry_too_large(self):
        metadata = {"title": "Title", "author": "Author"}
        transcript_info = {"language": "en", "source": "manual"}
        entries = [
            {"text": "y" * 1000, "start": 0.0, "duration": 1.0},
            {"text": "later line", "start": 1.0, "duration": 1.0},
        ]

        output = server._build_limited_output(
            metadata,
            entries,
            transcript_info,
            "dQw4w9WgXcQ",
            include_timestamps=False,
            max_chars=300,
        )

        # Nothing fits in the budget: kept_lines stays empty (the loop breaks on
        # the oversized first entry, so "later line" is never reached), the upper
        # bound still holds, and the truncation note is present.
        self.assertLessEqual(len(output), 300)
        self.assertNotIn("y" * 80, output)
        self.assertNotIn("later line", output)
        self.assertIn("打ち切りました", output)

    def test_build_limited_output_respects_limit_with_timestamps(self):
        metadata = {"title": "Title", "author": "Author"}
        transcript_info = {"language": "en", "source": "manual"}
        entries = [
            {"text": f"line {i}", "start": float(i), "duration": 1.0}
            for i in range(200)
        ]

        output = server._build_limited_output(
            metadata,
            entries,
            transcript_info,
            "dQw4w9WgXcQ",
            include_timestamps=True,
            max_chars=500,
        )

        self.assertLessEqual(len(output), 500)
        self.assertIn("打ち切りました", output)
        self.assertIn("[00:00] line 0", output)

    def test_build_limited_output_clamps_even_when_header_exceeds_budget(self):
        # Degenerate case: metadata alone is larger than max_chars. The output
        # must still be clamped to the limit.
        metadata = {"title": "T", "author": "A", "description": "d" * 5000}
        transcript_info = {"language": "en", "source": "manual"}
        entries = [{"text": "body line", "start": 0.0, "duration": 1.0}]

        output = server._build_limited_output(
            metadata,
            entries,
            transcript_info,
            "dQw4w9WgXcQ",
            include_timestamps=False,
            max_chars=300,
        )

        self.assertLessEqual(len(output), 300)


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
    def test_youtube_get_transcript_exposes_only_url_and_timestamps(self):
        params = inspect.signature(server.youtube_get_transcript).parameters
        self.assertEqual(list(params), ["url", "include_timestamps"])
        self.assertIs(params["include_timestamps"].default, False)

    async def test_youtube_get_transcript_uses_cache_and_always_fetches_metadata(self):
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
            ) as get_metadata,
        ):
            output = await server.youtube_get_transcript("dQw4w9WgXcQ")

        get_transcript.assert_not_called()
        get_metadata.assert_called_once_with("dQw4w9WgXcQ")
        self.assertIn("- Cached: true", output)
        self.assertIn("cached transcript", output)

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


class FrameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Per-test cache dir so a frame written by one test cannot be served
        # from disk by another (which would skip the mocked _extract_frame).
        self._tmp = tempfile.TemporaryDirectory()
        patcher = patch.object(
            server, "_get_cache_dir", return_value=Path(self._tmp.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    async def test_accepts_seconds_and_clock_timestamps(self):
        with (
            patch.object(server, "_stream_url", return_value="https://example/v.mp4"),
            patch.object(
                server, "_extract_frame", return_value=b"jpegbytes"
            ) as extract,
        ):
            for value in ("90", "01:30", "00:01:30", "20:14.5"):
                with self.subTest(value=value):
                    result = await server.youtube_get_frame("dQw4w9WgXcQ", value)
                    image, note = result
                    self.assertIsInstance(image, server.Image)
                    self.assertEqual(image.data, b"jpegbytes")
                    self.assertIn("Frame saved to:", note)
                    self.assertEqual(extract.call_args.args[1], value)

    async def test_serves_repeat_request_from_disk_without_reextracting(self):
        with (
            patch.object(server, "_stream_url", return_value="https://example/v.mp4"),
            patch.object(
                server, "_extract_frame", return_value=b"jpegbytes"
            ) as extract,
        ):
            await server.youtube_get_frame("dQw4w9WgXcQ", "90")
            result = await server.youtube_get_frame("dQw4w9WgXcQ", "90")

        image, _ = result
        self.assertEqual(image.data, b"jpegbytes")
        self.assertEqual(extract.call_count, 1)  # second call hit the disk cache

    async def test_rejects_timestamp_that_ffmpeg_would_read_as_an_option(self):
        with patch.object(server, "_extract_frame") as extract:
            result = await server.youtube_get_frame("dQw4w9WgXcQ", "-i")

        self.assertIsInstance(result, str)
        self.assertIn("Invalid timestamp", result)
        extract.assert_not_called()

    async def test_reports_extraction_failure_as_text(self):
        with (
            patch.object(server, "_stream_url", return_value="https://example/v.mp4"),
            patch.object(server, "_extract_frame", side_effect=RuntimeError("boom")),
        ):
            result = await server.youtube_get_frame("dQw4w9WgXcQ", "90")

        self.assertIsInstance(result, str)
        self.assertIn("boom", result)


class TranscriptFileTests(unittest.TestCase):
    def test_write_transcript_md_writes_timestamped_lines(self):
        entries = [
            {"text": "hello", "start": 0.0, "duration": 1.0},
            {"text": "", "start": 1.0, "duration": 1.0},
            {"text": "world", "start": 90.0, "duration": 1.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "_get_cache_dir", return_value=Path(tmp)):
                path = server._write_transcript_md("vid00000001", entries)
            text = Path(path).read_text(encoding="utf-8")
        self.assertEqual(text, "[00:00] hello\n[01:30] world")


if __name__ == "__main__":
    unittest.main()

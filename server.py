"""
YouTube Transcript MCP Server

Provides tools for extracting transcripts and metadata from YouTube videos.
Designed for use with Claude (claude.ai, Claude Code) to enable
"paste a URL and get the content" workflows similar to NotebookLM.
"""

import json
import re
import shutil
import subprocess
from enum import Enum
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = ["ja", "en", "ko", "zh", "de", "fr", "es", "pt", "ru"]
DEFAULT_LANGUAGES = ["ja", "en", "ko"]
MAX_TRANSCRIPT_CHARS = 200_000  # safety limit to avoid blowing up context

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("youtube_mcp")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_video_id(url_or_id: str) -> str:
    """Extract video ID from various YouTube URL formats or a bare ID."""
    url_or_id = url_or_id.strip()

    patterns = [
        r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)

    # Bare video ID
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", url_or_id):
        return url_or_id

    raise ValueError(
        f"Could not extract a YouTube video ID from: {url_or_id!r}. "
        "Please provide a valid YouTube URL or 11-character video ID."
    )


def _get_transcript_via_api(video_id: str, languages: list[str]) -> dict:
    """Use youtube-transcript-api (Python library) to fetch transcript."""
    from youtube_transcript_api import YouTubeTranscriptApi

    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

    # Try manual (human-created) transcripts first
    transcript = None
    source = "unknown"
    lang_found = "unknown"

    for lang in languages:
        try:
            transcript = transcript_list.find_manually_created_transcript([lang])
            source = "manual"
            lang_found = lang
            break
        except Exception:
            continue

    # Fall back to auto-generated
    if transcript is None:
        for lang in languages:
            try:
                transcript = transcript_list.find_generated_transcript([lang])
                source = "auto-generated"
                lang_found = lang
                break
            except Exception:
                continue

    if transcript is None:
        # Last resort: try any available transcript
        try:
            available = list(transcript_list)
            if available:
                transcript = available[0]
                source = "auto-generated" if transcript.is_generated else "manual"
                lang_found = transcript.language_code
        except Exception:
            pass

    if transcript is None:
        raise RuntimeError(
            f"No transcript available for video {video_id}. "
            f"Tried languages: {languages}. "
            "The video may not have captions enabled."
        )

    entries = transcript.fetch()
    return {
        "entries": [
            {
                "text": e.text,
                "start": round(e.start, 2),
                "duration": round(e.duration, 2),
            }
            for e in entries
        ],
        "language": lang_found,
        "source": source,
    }


def _get_transcript_via_ytdlp(video_id: str, languages: list[str]) -> dict:
    """Fallback: use yt-dlp CLI to fetch subtitles."""
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed. Install with: pip install yt-dlp")

    lang_str = ",".join(languages)
    url = f"https://www.youtube.com/watch?v={video_id}"

    # Try manual subs first, then auto
    for sub_flag in ["--write-sub", "--write-auto-sub"]:
        result = subprocess.run(
            [
                "yt-dlp",
                sub_flag,
                "--sub-lang",
                lang_str,
                "--sub-format",
                "json3",
                "--skip-download",
                "--no-warnings",
                "-o",
                f"/tmp/yt_{video_id}",
                "--print-json",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            continue

        # Find the subtitle file
        import glob

        sub_files = glob.glob(f"/tmp/yt_{video_id}*.json3")
        if not sub_files:
            continue

        with open(sub_files[0], "r", encoding="utf-8") as f:
            sub_data = json.load(f)

        # Clean up
        for sf in sub_files:
            import os

            os.remove(sf)

        entries = []
        for event in sub_data.get("events", []):
            text_parts = []
            for seg in event.get("segs", []):
                t = seg.get("utf8", "").strip()
                if t:
                    text_parts.append(t)
            if text_parts:
                entries.append(
                    {
                        "text": " ".join(text_parts),
                        "start": round(event.get("tStartMs", 0) / 1000, 2),
                        "duration": round(event.get("dDurationMs", 0) / 1000, 2),
                    }
                )

        source = "manual" if sub_flag == "--write-sub" else "auto-generated"
        return {"entries": entries, "language": "detected", "source": source}

    raise RuntimeError(
        f"yt-dlp could not find subtitles for {video_id} in languages: {languages}"
    )


def _get_metadata_via_ytdlp(video_id: str) -> dict:
    """Fetch video metadata using yt-dlp --dump-json."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    if not shutil.which("yt-dlp"):
        return {"title": "Unknown", "author": "Unknown", "video_id": video_id}

    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", "--no-warnings", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return {
                "title": data.get("title", "Unknown"),
                "author": data.get("uploader", data.get("channel", "Unknown")),
                "channel_url": data.get("channel_url", ""),
                "upload_date": data.get("upload_date", ""),
                "duration_seconds": data.get("duration"),
                "description": (data.get("description", "") or "")[:500],
                "view_count": data.get("view_count"),
                "video_id": video_id,
            }
    except Exception:
        pass

    return {"title": "Unknown", "author": "Unknown", "video_id": video_id}


def _get_metadata_via_api(video_id: str) -> dict:
    """Fetch basic metadata using youtube-transcript-api's page scraping."""
    # youtube-transcript-api doesn't provide metadata, so we do a minimal scrape
    import html
    import urllib.request

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="replace")

        title_match = re.search(r"<title>(.*?)</title>", page)
        title = (
            html.unescape(title_match.group(1)).replace(" - YouTube", "").strip()
            if title_match
            else "Unknown"
        )

        author_match = re.search(r'"ownerChannelName":"(.*?)"', page)
        author = html.unescape(author_match.group(1)) if author_match else "Unknown"

        return {"title": title, "author": author, "video_id": video_id}
    except Exception:
        return {"title": "Unknown", "author": "Unknown", "video_id": video_id}


def _format_transcript_text(entries: list[dict], include_timestamps: bool) -> str:
    """Format transcript entries into readable text."""
    lines = []
    for e in entries:
        text = e["text"].strip()
        if not text:
            continue
        if include_timestamps:
            minutes = int(e["start"] // 60)
            seconds = int(e["start"] % 60)
            lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _build_markdown_output(
    metadata: dict,
    transcript_text: str,
    transcript_info: dict,
    video_id: str,
) -> str:
    """Build the final Markdown output with YAML frontmatter."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    frontmatter_fields = [
        f'title: "{metadata.get("title", "Unknown")}"',
        f'author: "{metadata.get("author", "Unknown")}"',
        f"url: {url}",
        f"video_id: {video_id}",
        f"transcript_language: {transcript_info.get('language', 'unknown')}",
        f"transcript_source: {transcript_info.get('source', 'unknown')}",
    ]

    if metadata.get("upload_date"):
        d = metadata["upload_date"]
        frontmatter_fields.append(
            f"upload_date: {d[:4]}-{d[4:6]}-{d[6:]}"
            if len(d) == 8
            else f"upload_date: {d}"
        )
    if metadata.get("duration_seconds"):
        m, s = divmod(int(metadata["duration_seconds"]), 60)
        frontmatter_fields.append(f"duration: {m}m{s}s")

    frontmatter = "---\n" + "\n".join(frontmatter_fields) + "\n---"

    description_section = ""
    if metadata.get("description"):
        description_section = f"\n## Description\n\n{metadata['description']}\n"

    output = f"""{frontmatter}
{description_section}
## Transcript

{transcript_text}
"""
    # Truncate if too large
    if len(output) > MAX_TRANSCRIPT_CHARS:
        output = (
            output[:MAX_TRANSCRIPT_CHARS]
            + "\n\n[... transcript truncated due to length ...]"
        )

    return output


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------


class GetTranscriptInput(BaseModel):
    """Input for fetching a YouTube video transcript."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(
        ...,
        description=(
            "YouTube video URL or video ID. "
            "Accepts formats: youtube.com/watch?v=..., youtu.be/..., "
            "youtube.com/shorts/..., or a bare 11-char video ID."
        ),
        min_length=1,
    )
    languages: Optional[list[str]] = Field(
        default=None,
        description=(
            f"Preferred languages in priority order. Defaults to {DEFAULT_LANGUAGES}. "
            f"Supported: {SUPPORTED_LANGUAGES} (and others available on the video)."
        ),
    )
    include_timestamps: bool = Field(
        default=False,
        description="Include [MM:SS] timestamps for each line of the transcript.",
    )
    include_metadata: bool = Field(
        default=True,
        description="Include video metadata (title, author, etc.) in the output.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip().strip("<>")  # strip angle brackets some people paste
        return v


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="youtube_get_transcript",
    annotations={
        "title": "Get YouTube Transcript",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def youtube_get_transcript(params: GetTranscriptInput) -> str:
    """Fetch the transcript (subtitles) of a YouTube video.

    Returns the video's transcript as Markdown with YAML frontmatter containing
    metadata (title, author, URL). Tries human-created subtitles first, then
    falls back to auto-generated captions.

    Useful for: summarizing videos, fact-checking claims, extracting key points,
    translating content, creating notes from lectures/talks.

    Args:
        params (GetTranscriptInput): Contains:
            - url (str): YouTube URL or video ID
            - languages (list[str]): Preferred languages, defaults to ["ja", "en"]
            - include_timestamps (bool): Add [MM:SS] timestamps per line
            - include_metadata (bool): Include title/author/description

    Returns:
        str: Markdown-formatted transcript with YAML frontmatter
    """
    video_id = _extract_video_id(params.url)
    languages = params.languages or DEFAULT_LANGUAGES

    # --- Fetch transcript ---
    transcript_info = None
    errors = []

    # Strategy 1: youtube-transcript-api (preferred, lightweight)
    try:
        transcript_info = _get_transcript_via_api(video_id, languages)
    except Exception as e:
        errors.append(f"youtube-transcript-api: {e}")

    # Strategy 2: yt-dlp fallback
    if transcript_info is None:
        try:
            transcript_info = _get_transcript_via_ytdlp(video_id, languages)
        except Exception as e:
            errors.append(f"yt-dlp: {e}")

    if transcript_info is None:
        return (
            f"Error: Could not retrieve transcript for video {video_id}.\n"
            f"Attempted methods:\n" + "\n".join(f"  - {e}" for e in errors) + "\n\n"
            "Possible causes:\n"
            "  - The video has no captions/subtitles\n"
            "  - The video is private or age-restricted\n"
            "  - The requested languages are not available"
        )

    transcript_text = _format_transcript_text(
        transcript_info["entries"], params.include_timestamps
    )

    # --- Fetch metadata ---
    metadata = {"title": "Unknown", "author": "Unknown", "video_id": video_id}
    if params.include_metadata:
        try:
            metadata = _get_metadata_via_ytdlp(video_id)
        except Exception:
            try:
                metadata = _get_metadata_via_api(video_id)
            except Exception:
                pass

    return _build_markdown_output(metadata, transcript_text, transcript_info, video_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()

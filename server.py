"""YouTube Transcript MCP Server"""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

DEFAULT_LANGUAGES = ["ja", "en", "ko"]
MAX_TRANSCRIPT_CHARS = 200_000  # safety limit to avoid blowing up context
CACHE_TTL_DAYS = 180

mcp = FastMCP("yt-transcript-mcp")


def _extract_video_id(url_or_id: str) -> str:
    """Extract video ID from various YouTube URL formats or a bare ID."""
    url_or_id = url_or_id.strip()

    match = re.search(
        r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
        url_or_id,
    )
    if match:
        return match.group(1)

    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", url_or_id):
        return url_or_id

    raise ValueError(
        f"Could not extract a YouTube video ID from: {url_or_id!r}. "
        "Please provide a valid YouTube URL or 11-character video ID."
    )


def _get_transcript(video_id: str, languages: list[str]) -> dict:
    """Fetch transcript using youtube-transcript-api."""
    from youtube_transcript_api import YouTubeTranscriptApi

    transcript_list = YouTubeTranscriptApi().list(video_id)

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


def _get_metadata(video_id: str, *, full_description: bool = False) -> dict:
    """Fetch video metadata using yt-dlp --dump-json."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    if not shutil.which("yt-dlp"):
        return {"title": "Unknown", "author": "Unknown", "video_id": video_id}

    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--skip-download", "--no-warnings", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            description = data.get("description", "") or ""
            if not full_description:
                description = description[:500]
            return {
                "title": data.get("title", "Unknown"),
                "author": data.get("uploader", data.get("channel", "Unknown")),
                "channel_url": data.get("channel_url", ""),
                "upload_date": data.get("upload_date", ""),
                "duration_seconds": data.get("duration"),
                "description": description,
                "view_count": data.get("view_count"),
                "video_id": video_id,
            }
    except Exception:
        pass

    return {"title": "Unknown", "author": "Unknown", "video_id": video_id}


def _format_transcript(entries: list[dict], include_timestamps: bool) -> str:
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


def _build_output(
    metadata: dict,
    transcript_text: str,
    transcript_info: dict,
    video_id: str,
    *,
    cached: bool = False,
    cached_at: str = "",
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

    if cached:
        frontmatter_fields.append("cached: true")
        frontmatter_fields.append(f"cached_at: {cached_at}")

    frontmatter = "---\n" + "\n".join(frontmatter_fields) + "\n---"

    description_section = ""
    if metadata.get("description"):
        description_section = f"\n## Description\n\n{metadata['description']}\n"

    output = f"""{frontmatter}
{description_section}
## Transcript

{transcript_text}
"""
    if len(output) > MAX_TRANSCRIPT_CHARS:
        output = (
            output[:MAX_TRANSCRIPT_CHARS]
            + "\n\n[... transcript truncated due to length ...]"
        )

    return output


# ── Cache helpers ──────────────────────────────────────────────────────────────


def _get_cache_dir() -> Path:
    cache_env = os.environ.get("CACHE_DIR")
    if cache_env:
        return Path(cache_env)
    return Path(__file__).parent / ".transcript_cache"


def _cache_path(video_id: str, languages: list[str]) -> Path:
    return _get_cache_dir() / f"{video_id}_{'_'.join(languages)}.json"


def _load_cache(video_id: str, languages: list[str]) -> dict | None:
    """Return cached entry or None on miss, expiry, or corruption."""
    path = _cache_path(video_id, languages)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = date.fromisoformat(data["cached_at"])
        if (date.today() - cached_at).days > CACHE_TTL_DAYS:
            return None
        return data
    except Exception:
        return None


def _save_cache(video_id: str, languages: list[str], transcript_info: dict) -> None:
    """Persist transcript_info to a JSON cache file. Failures are non-fatal."""
    try:
        cache_dir = _get_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": transcript_info["entries"],
            "language": transcript_info["language"],
            "source": transcript_info["source"],
            "cached_at": date.today().isoformat(),
            "ttl_days": CACHE_TTL_DAYS,
        }
        _cache_path(video_id, languages).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ── MCP tools ─────────────────────────────────────────────────────────────────


@mcp.tool(
    name="youtube_get_transcript",
    annotations=ToolAnnotations(
        title="Get YouTube Transcript",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def youtube_get_transcript(
    url: str,
    languages: Optional[list[str]] = None,
    include_timestamps: bool = False,
    include_metadata: bool = True,
) -> str:
    """Fetch the transcript (subtitles) of a YouTube video.

    Returns the video's transcript as Markdown with YAML frontmatter containing
    metadata (title, author, URL). Tries human-created subtitles first, then
    falls back to auto-generated captions. Uses a local cache (stdio mode) to
    avoid re-fetching transcripts for previously seen videos.

    Useful for: summarizing videos, fact-checking claims, extracting key points,
    translating content, creating notes from lectures/talks.

    Args:
        url: YouTube video URL or video ID. Accepts youtube.com/watch?v=...,
            youtu.be/..., youtube.com/shorts/..., or a bare 11-char video ID.
        languages: Preferred languages in priority order.
            Defaults to ["ja", "en", "ko"].
        include_timestamps: Include [MM:SS] timestamps for each line of the transcript.
        include_metadata: Include video metadata (title, author, etc.) in the output.

    Returns:
        str: Markdown-formatted transcript with YAML frontmatter
    """
    url = url.strip().strip("<>")
    video_id = _extract_video_id(url)
    langs = languages or DEFAULT_LANGUAGES

    # Check cache before fetching from YouTube
    cached_entry = _load_cache(video_id, langs)

    if cached_entry is not None:
        transcript_info = {
            "entries": cached_entry["entries"],
            "language": cached_entry["language"],
            "source": cached_entry["source"],
        }
        was_cached = True
        cached_at = cached_entry["cached_at"]
    else:
        try:
            transcript_info = _get_transcript(video_id, langs)
        except Exception as e:
            return (
                f"Error: Could not retrieve transcript for video {video_id}.\n{e}\n\n"
                "Possible causes:\n"
                "  - The video has no captions/subtitles\n"
                "  - The video is private or age-restricted\n"
                "  - The requested languages are not available"
            )
        _save_cache(video_id, langs, transcript_info)
        was_cached = False
        cached_at = ""

    transcript_text = _format_transcript(transcript_info["entries"], include_timestamps)

    metadata = {"title": "Unknown", "author": "Unknown", "video_id": video_id}
    if include_metadata:
        metadata = _get_metadata(video_id)

    return _build_output(
        metadata,
        transcript_text,
        transcript_info,
        video_id,
        cached=was_cached,
        cached_at=cached_at,
    )


@mcp.tool(
    name="youtube_get_video_info",
    annotations=ToolAnnotations(
        title="Get YouTube Video Info",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def youtube_get_video_info(url: str) -> str:
    """Fetch metadata for a YouTube video without downloading the transcript.

    Returns video information as a JSON string. Useful for checking video
    credibility (upload date, view count, channel), reading the full description
    for links and resources, or getting a quick overview before deciding whether
    to fetch the full transcript.

    Args:
        url: YouTube video URL or video ID. Accepts youtube.com/watch?v=...,
            youtu.be/..., youtube.com/shorts/..., or a bare 11-char video ID.

    Returns:
        str: JSON string with fields: video_id, title, author, channel_url,
            upload_date, duration_seconds, description (full text), view_count
    """
    url = url.strip().strip("<>")
    video_id = _extract_video_id(url)
    metadata = _get_metadata(video_id, full_description=True)
    return json.dumps(metadata, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport == "streamable-http":
        import uvicorn
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        app = mcp.streamable_http_app()

        api_key = os.environ.get("API_KEY")
        if api_key:

            class _BearerAuthMiddleware(BaseHTTPMiddleware):
                async def dispatch(self, request: Request, call_next):
                    auth = request.headers.get("Authorization", "")
                    if not auth.startswith("Bearer ") or auth[7:] != api_key:
                        return JSONResponse(
                            {"error": "unauthorized"},
                            status_code=401,
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    return await call_next(request)

            app.add_middleware(_BearerAuthMiddleware)

        port = int(os.environ.get("PORT", "8000"))
        uvicorn.run(app, host="localhost", port=port)
    else:
        mcp.run()  # stdio (default)

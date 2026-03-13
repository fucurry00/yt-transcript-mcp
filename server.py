"""YouTube Transcript MCP Server"""

import json
import re
import shutil
import subprocess
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

DEFAULT_LANGUAGES = ["ja", "en", "ko"]
MAX_TRANSCRIPT_CHARS = 200_000  # safety limit to avoid blowing up context

mcp = FastMCP("yt-transcript-mcp")


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


def _get_metadata(video_id: str) -> dict:
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
    metadata: dict, transcript_text: str, transcript_info: dict, video_id: str
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
    if len(output) > MAX_TRANSCRIPT_CHARS:
        output = (
            output[:MAX_TRANSCRIPT_CHARS]
            + "\n\n[... transcript truncated due to length ...]"
        )

    return output


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
    falls back to auto-generated captions.

    Useful for: summarizing videos, fact-checking claims, extracting key points,
    translating content, creating notes from lectures/talks.

    Args:
        url: YouTube video URL or video ID. Accepts youtube.com/watch?v=...,
            youtu.be/..., youtube.com/shorts/..., or a bare 11-char video ID.
        languages: Preferred languages in priority order. Defaults to ["ja", "en", "ko"].
        include_timestamps: Include [MM:SS] timestamps for each line of the transcript.
        include_metadata: Include video metadata (title, author, etc.) in the output.

    Returns:
        str: Markdown-formatted transcript with YAML frontmatter
    """
    url = url.strip().strip("<>")
    video_id = _extract_video_id(url)
    langs = languages or DEFAULT_LANGUAGES

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

    transcript_text = _format_transcript(transcript_info["entries"], include_timestamps)

    metadata = {"title": "Unknown", "author": "Unknown", "video_id": video_id}
    if include_metadata:
        metadata = _get_metadata(video_id)

    return _build_output(metadata, transcript_text, transcript_info, video_id)


if __name__ == "__main__":
    import os

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
    elif transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run()

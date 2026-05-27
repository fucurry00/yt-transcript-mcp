"""YouTube Transcript MCP Server"""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

DEFAULT_LANGUAGES = ["ja", "en", "ko"]
MAX_TRANSCRIPT_CHARS = 200_000  # safety limit to avoid blowing up context
MIN_TRANSCRIPT_CHARS = 10_000
CACHE_TTL_DAYS = 180
VIDEO_ID_RE = re.compile(r"[a-zA-Z0-9_-]{11}")

mcp = FastMCP("yt-transcript-mcp")


def _validate_video_id(candidate: str, original: str) -> str:
    if VIDEO_ID_RE.fullmatch(candidate):
        return candidate

    raise ValueError(
        f"Could not extract a YouTube video ID from: {original!r}. "
        "Please provide a valid YouTube URL or 11-character video ID."
    )


def _extract_video_id(url_or_id: str) -> str:
    """Extract video ID from various YouTube URL formats or a bare ID."""
    url_or_id = url_or_id.strip()

    if VIDEO_ID_RE.fullmatch(url_or_id):
        return url_or_id

    parsed = urlparse(url_or_id)
    host = parsed.hostname.lower() if parsed.hostname else ""
    path_parts = [part for part in parsed.path.split("/") if part]

    if host == "youtu.be" and path_parts:
        return _validate_video_id(path_parts[0], url_or_id)

    is_youtube_host = host == "youtube.com" or host.endswith(".youtube.com")
    if is_youtube_host:
        if parsed.path == "/watch":
            query = parse_qs(parsed.query)
            if query.get("v"):
                return _validate_video_id(query["v"][0], url_or_id)
        if len(path_parts) >= 2 and path_parts[0] in {
            "embed",
            "live",
            "shorts",
            "v",
        }:
            return _validate_video_id(path_parts[1], url_or_id)

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


def _metadata_error_response(video_id: str, metadata_error: dict) -> dict:
    return {
        "title": "Unknown",
        "author": "Unknown",
        "video_id": video_id,
        "metadata_error": metadata_error,
    }


def _get_metadata(video_id: str, *, full_description: bool = False) -> dict:
    """Fetch video metadata using yt-dlp --dump-json."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    if not shutil.which("yt-dlp"):
        return _metadata_error_response(
            video_id,
            {
                "type": "yt_dlp_not_found",
                "message": "yt-dlp executable was not found on PATH",
            },
        )

    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--skip-download", "--no-warnings", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as e:
        return _metadata_error_response(
            video_id,
            {
                "type": "yt_dlp_timeout",
                "message": str(e),
                "timeout_seconds": e.timeout,
            },
        )
    except Exception as e:
        return _metadata_error_response(
            video_id,
            {
                "type": "yt_dlp_exception",
                "message": str(e),
            },
        )

    if result.returncode != 0:
        return _metadata_error_response(
            video_id,
            {
                "type": "yt_dlp_failed",
                "returncode": result.returncode,
                "stderr": (result.stderr or "")[-2000:],
            },
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return _metadata_error_response(
            video_id,
            {
                "type": "yt_dlp_invalid_json",
                "message": str(e),
                "stdout": (result.stdout or "")[-2000:],
            },
        )

    description = data.get("description", "") or ""
    if not full_description:
        description = description[:500]
    return {
        "title": data.get("title", "Unknown"),
        "author": data.get("uploader") or data.get("channel") or "Unknown",
        "channel_url": data.get("channel_url", ""),
        "upload_date": data.get("upload_date", ""),
        "duration_seconds": data.get("duration"),
        "description": description,
        "view_count": data.get("view_count"),
        "video_id": video_id,
    }


def _format_transcript(entries: list[dict], include_timestamps: bool) -> str:
    """Format transcript entries into readable text."""
    lines = []
    for e in entries:
        text = e["text"].strip()
        if not text:
            continue
        if include_timestamps:
            h = int(e["start"] // 3600)
            m = int((e["start"] % 3600) // 60)
            s = int(e["start"] % 60)
            ts = f"[{h:02d}:{m:02d}:{s:02d}]" if h > 0 else f"[{m:02d}:{s:02d}]"
            lines.append(f"{ts} {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _format_seconds(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _validate_transcript_options(
    start_seconds: float | None,
    end_seconds: float | None,
    max_chars: int,
) -> str | None:
    if max_chars < MIN_TRANSCRIPT_CHARS or max_chars > MAX_TRANSCRIPT_CHARS:
        return (
            "Error: max_chars must be between "
            f"{MIN_TRANSCRIPT_CHARS} and {MAX_TRANSCRIPT_CHARS}."
        )

    if start_seconds is not None and start_seconds < 0:
        return "Error: start_seconds must be greater than or equal to 0."
    if end_seconds is not None and end_seconds < 0:
        return "Error: end_seconds must be greater than or equal to 0."
    if (
        start_seconds is not None
        and end_seconds is not None
        and start_seconds >= end_seconds
    ):
        return "Error: start_seconds must be less than end_seconds."

    return None


def _entry_overlaps_range(
    entry: dict,
    start_seconds: float | None,
    end_seconds: float | None,
) -> bool:
    entry_start = float(entry["start"])
    entry_end = entry_start + float(entry.get("duration", 0))

    if start_seconds is not None and entry_end <= start_seconds:
        return False
    if end_seconds is not None and entry_start >= end_seconds:
        return False
    return True


def _filter_entries_by_range(
    entries: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    if start_seconds is None and end_seconds is None:
        return entries
    return [
        entry
        for entry in entries
        if _entry_overlaps_range(entry, start_seconds, end_seconds)
    ]


def _markdown_metadata_value(value: object) -> str:
    """Return a single-line value for Markdown metadata bullets."""
    return " ".join(str(value).splitlines()).strip()


def _format_transcript_range(
    start_seconds: float | None,
    end_seconds: float | None,
) -> str | None:
    if start_seconds is None and end_seconds is None:
        return None
    start = "0" if start_seconds is None else _format_seconds(start_seconds)
    end = "end" if end_seconds is None else _format_seconds(end_seconds)
    return f"{start}s-{end}s"


def _build_continuation_section(
    video_id: str,
    next_start_seconds: float,
    max_chars: int,
    end_seconds: float | None,
) -> str:
    next_start = _format_seconds(next_start_seconds)
    call_parts = [
        f'url="https://www.youtube.com/watch?v={video_id}"',
        f"start_seconds={next_start}",
    ]
    if end_seconds is not None:
        call_parts.append(f"end_seconds={_format_seconds(end_seconds)}")
    call_parts.append(f"max_chars={max_chars}")

    return (
        "\n## Continuation\n\n"
        "- Transcript truncated: true\n"
        f"- next_start_seconds: {next_start}\n"
        f"- max_chars: {max_chars}\n"
        f"- Suggested next call: youtube_get_transcript({', '.join(call_parts)})\n"
    )


def _build_output(
    metadata: dict,
    transcript_text: str,
    transcript_info: dict,
    video_id: str,
    *,
    cached: bool = False,
    cached_at: str = "",
    transcript_range: str | None = None,
    continuation_section: str = "",
    max_chars: int | None = None,
) -> str:
    """Build the final Markdown output."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    title = _markdown_metadata_value(metadata.get("title", "Unknown")) or "Unknown"
    metadata_lines = [
        f"- Author: {_markdown_metadata_value(metadata.get('author', 'Unknown'))}",
        f"- URL: {url}",
        f"- Video ID: {video_id}",
        (
            "- Transcript language: "
            f"{_markdown_metadata_value(transcript_info.get('language', 'unknown'))}"
        ),
        (
            "- Transcript source: "
            f"{_markdown_metadata_value(transcript_info.get('source', 'unknown'))}"
        ),
    ]
    if transcript_range is not None:
        metadata_lines.append(f"- Transcript range: {transcript_range}")

    if metadata.get("upload_date"):
        d = metadata["upload_date"]
        upload_date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d
        metadata_lines.append(f"- Upload date: {_markdown_metadata_value(upload_date)}")
    if metadata.get("duration_seconds"):
        m, s = divmod(int(metadata["duration_seconds"]), 60)
        metadata_lines.append(f"- Duration: {m}m{s}s")

    if cached:
        metadata_lines.append("- Cached: true")
        metadata_lines.append(f"- Cached at: {_markdown_metadata_value(cached_at)}")

    if metadata.get("metadata_error"):
        err = metadata["metadata_error"]
        metadata_lines.append(
            f"- Metadata error: {_markdown_metadata_value(err.get('type', 'unknown'))}"
        )

    description_section = ""
    if metadata.get("description"):
        escaped = "\n".join(
            f"\\{line}" if line.startswith("#") else line
            for line in metadata["description"].splitlines()
        )
        description_section = f"\n## Description\n\n{escaped}\n"

    metadata_section = "\n".join(metadata_lines)
    output = f"""# {title}

{metadata_section}
{description_section}
## Transcript

{transcript_text}
"""

    return output + continuation_section


def _build_limited_output(
    metadata: dict,
    entries: list[dict],
    transcript_info: dict,
    video_id: str,
    *,
    include_timestamps: bool,
    cached: bool = False,
    cached_at: str = "",
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    max_chars: int = MAX_TRANSCRIPT_CHARS,
) -> str:
    """Build Markdown output without splitting transcript entries."""
    transcript_range = _format_transcript_range(start_seconds, end_seconds)
    display_entries = [entry for entry in entries if entry["text"].strip()]

    if not display_entries:
        transcript_text = "No transcript entries found for the requested range."
        return _build_output(
            metadata,
            transcript_text,
            transcript_info,
            video_id,
            cached=cached,
            cached_at=cached_at,
            transcript_range=transcript_range,
            max_chars=max_chars,
        )

    full_text = _format_transcript(display_entries, include_timestamps)
    full_output = _build_output(
        metadata,
        full_text,
        transcript_info,
        video_id,
        cached=cached,
        cached_at=cached_at,
        transcript_range=transcript_range,
        max_chars=max_chars,
    )
    if len(full_output) <= max_chars:
        return full_output

    header_output = _build_output(
        metadata,
        "",
        transcript_info,
        video_id,
        cached=cached,
        cached_at=cached_at,
        transcript_range=transcript_range,
        max_chars=max_chars,
    )
    header_size = len(header_output)

    kept_lines: list[str] = []
    kept_entries: list[dict] = []
    for index, entry in enumerate(display_entries):
        if include_timestamps:
            h = int(entry["start"] // 3600)
            m = int((entry["start"] % 3600) // 60)
            s = int(entry["start"] % 60)
            ts = f"[{h:02d}:{m:02d}:{s:02d}]" if h > 0 else f"[{m:02d}:{s:02d}]"
            line = f"{ts} {entry['text'].strip()}"
        else:
            line = entry["text"].strip()

        new_line_size = len(line) + (1 if kept_lines else 0)
        next_entry = (
            display_entries[index + 1] if index + 1 < len(display_entries) else None
        )
        continuation_size = 0
        if next_entry is not None:
            continuation_size = len(
                _build_continuation_section(
                    video_id, float(next_entry["start"]), max_chars, end_seconds
                )
            )

        current_text_size = sum(len(ln) for ln in kept_lines) + (
            len(kept_lines) if kept_lines else 0
        )
        if (
            header_size + current_text_size + new_line_size + continuation_size
            > max_chars
        ):
            break

        kept_lines.append(line)
        kept_entries.append(entry)

    if not kept_entries:
        transcript_text = (
            "The first transcript entry is too large to display within the "
            f"max_chars={max_chars} limit. Try increasing max_chars or "
            "requesting a smaller time range."
        )
        return _build_output(
            metadata,
            transcript_text,
            transcript_info,
            video_id,
            cached=cached,
            cached_at=cached_at,
            transcript_range=transcript_range,
            max_chars=max_chars,
        )

    next_index = len(kept_entries)
    if next_index >= len(display_entries):
        return _build_output(
            metadata,
            "\n".join(kept_lines),
            transcript_info,
            video_id,
            cached=cached,
            cached_at=cached_at,
            transcript_range=transcript_range,
            max_chars=max_chars,
        )

    continuation = _build_continuation_section(
        video_id,
        float(display_entries[next_index]["start"]),
        max_chars,
        end_seconds,
    )
    return _build_output(
        metadata,
        "\n".join(kept_lines),
        transcript_info,
        video_id,
        cached=cached,
        cached_at=cached_at,
        transcript_range=transcript_range,
        continuation_section=continuation,
        max_chars=max_chars,
    )


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
        if not all(k in data for k in ("entries", "language", "source", "cached_at")):
            return None
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
    start_seconds: Optional[float] = None,
    end_seconds: Optional[float] = None,
    max_chars: int = MAX_TRANSCRIPT_CHARS,
) -> str:
    """Fetch the transcript (subtitles) of a YouTube video.

    Returns the video's transcript as Markdown with metadata rendered as normal
    Markdown content. Tries human-created subtitles first, then falls back to
    auto-generated captions. Uses a local cache (stdio mode) to avoid re-fetching
    transcripts for previously seen videos.

    Useful for: summarizing videos, fact-checking claims, extracting key points,
    translating content, creating notes from lectures/talks.

    Args:
        url: YouTube video URL or video ID. Accepts youtube.com/watch?v=...,
            youtu.be/..., youtube.com/shorts/..., or a bare 11-char video ID.
        languages: Preferred languages in priority order.
            Defaults to ["ja", "en", "ko"].
        include_timestamps: Include [MM:SS] timestamps for each line of the transcript.
        include_metadata: Include video metadata (title, author, etc.) in the output.
        start_seconds: Start of the transcript range, in seconds.
        end_seconds: End of the transcript range, in seconds.
        max_chars: Maximum final Markdown output size. Must be 10,000 to 200,000.

    Returns:
        str: Markdown-formatted transcript
    """
    url = url.strip().strip("<>")
    try:
        video_id = _extract_video_id(url)
    except ValueError as e:
        return str(e)
    langs = languages or DEFAULT_LANGUAGES
    options_error = _validate_transcript_options(
        start_seconds,
        end_seconds,
        max_chars,
    )
    if options_error is not None:
        return options_error

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

    selected_entries = _filter_entries_by_range(
        transcript_info["entries"],
        start_seconds,
        end_seconds,
    )

    metadata = {"title": "Unknown", "author": "Unknown", "video_id": video_id}
    if include_metadata:
        metadata = _get_metadata(video_id)

    return _build_limited_output(
        metadata,
        selected_entries,
        transcript_info,
        video_id,
        include_timestamps=include_timestamps,
        cached=was_cached,
        cached_at=cached_at,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        max_chars=max_chars,
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
    try:
        video_id = _extract_video_id(url)
    except ValueError as e:
        return str(e)
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

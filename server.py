"""YouTube Transcript MCP Server"""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations

DEFAULT_LANGUAGES = ["ja", "en", "ko"]
MAX_TRANSCRIPT_CHARS = 200_000  # safety limit to avoid blowing up context
CACHE_TTL_DAYS = 180
VIDEO_ID_RE = re.compile(r"[a-zA-Z0-9_-]{11}")

# Seconds ("1234"), or MM:SS / HH:MM:SS, with optional fractional part.
TIMESTAMP_RE = re.compile(r"\d+(:\d{1,2}){0,2}(\.\d+)?")
# Video-only: frames need no audio, and it is smaller than 360p+audio.
# Capped at 720p, which resolves on-screen code where 360p does not.
# avc1 first: it decodes ~25% faster than the av01 of the same resolution.
# protocol^=http on every tier keeps yt-dlp off HLS, whose m3u8 manifest
# ffmpeg cannot open here. Each tier has been checked to decode.
FRAME_FORMAT = (
    "bv*[height<=720][vcodec^=avc1][protocol^=http]"
    "/bv*[height<=720][protocol^=http]"
    "/b[height<=720][protocol^=http]"
)

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


def _format_line(entry: dict, include_timestamps: bool) -> str:
    """Format a single transcript entry into a readable line."""
    text = entry["text"].strip()
    if not include_timestamps:
        return text
    h = int(entry["start"] // 3600)
    m = int((entry["start"] % 3600) // 60)
    s = int(entry["start"] % 60)
    ts = f"[{h:02d}:{m:02d}:{s:02d}]" if h > 0 else f"[{m:02d}:{s:02d}]"
    return f"{ts} {text}"


def _format_transcript(entries: list[dict], include_timestamps: bool) -> str:
    """Format transcript entries into readable text, skipping blank entries."""
    return "\n".join(
        _format_line(e, include_timestamps) for e in entries if e["text"].strip()
    )


def _markdown_metadata_value(value: object) -> str:
    """Return a single-line value for Markdown metadata bullets."""
    return " ".join(str(value).splitlines()).strip()


def _truncation_note(max_chars: int) -> str:
    """Note appended when the transcript is truncated to fit max_chars."""
    return (
        f"\n\n*※ 文字起こしが長いため先頭約{max_chars:,}文字で打ち切りました。"
        "このツールは先頭部分のみ返します。*\n"
    )


def _build_output(
    metadata: dict,
    transcript_text: str,
    transcript_info: dict,
    video_id: str,
    *,
    cached: bool = False,
    cached_at: str = "",
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
    return f"""# {title}

{metadata_section}
{description_section}
## Transcript

{transcript_text}
"""


def _build_limited_output(
    metadata: dict,
    entries: list[dict],
    transcript_info: dict,
    video_id: str,
    *,
    include_timestamps: bool,
    cached: bool = False,
    cached_at: str = "",
    max_chars: int = MAX_TRANSCRIPT_CHARS,
) -> str:
    """Build Markdown output, truncating on entry boundaries if too long."""
    display_entries = [entry for entry in entries if entry["text"].strip()]

    if not display_entries:
        return _build_output(
            metadata,
            "No transcript entries found.",
            transcript_info,
            video_id,
            cached=cached,
            cached_at=cached_at,
        )

    full_output = _build_output(
        metadata,
        _format_transcript(display_entries, include_timestamps),
        transcript_info,
        video_id,
        cached=cached,
        cached_at=cached_at,
    )
    if len(full_output) <= max_chars:
        return full_output

    # Too long: keep whole entries from the start until the budget runs out.
    # The character budget reserves room for the header and the truncation note.
    note = _truncation_note(max_chars)
    header_size = len(
        _build_output(
            metadata,
            "",
            transcript_info,
            video_id,
            cached=cached,
            cached_at=cached_at,
        )
    )
    budget = max(0, max_chars - header_size - len(note))

    kept_lines: list[str] = []
    used = 0
    for entry in display_entries:
        line = _format_line(entry, include_timestamps)
        new_line_size = len(line) + (1 if kept_lines else 0)
        if used + new_line_size > budget:
            break
        kept_lines.append(line)
        used += new_line_size

    output = (
        _build_output(
            metadata,
            "\n".join(kept_lines),
            transcript_info,
            video_id,
            cached=cached,
            cached_at=cached_at,
        )
        + note
    )
    # Final safety clamp: unconditionally guarantee len(output) <= max_chars,
    # even in the degenerate case where the header alone exceeds the budget.
    return output[:max_chars]


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


# ── Frame extraction ──────────────────────────────────────────────────────────


def _stream_url(video_id: str) -> str:
    """Resolve a direct video stream URL via yt-dlp.

    The URL carries an `expire` param (hours), so it cannot be cached.
    """
    result = subprocess.run(
        [
            "yt-dlp",
            "-f",
            FRAME_FORMAT,
            "-g",
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp could not resolve a stream URL: {result.stderr[-500:]}"
        )

    url = result.stdout.strip().splitlines()
    if not url:
        raise RuntimeError("yt-dlp returned no stream URL.")
    return url[0]


def _extract_frame(stream_url: str, timestamp: str) -> bytes:
    """Grab one JPEG frame at timestamp. ffmpeg range-reads only what it needs."""
    import imageio_ffmpeg  # type: ignore[import-untyped]

    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-loglevel",
            "error",
            # -ss before -i seeks on the input, so ffmpeg fetches only the bytes
            # around the timestamp instead of streaming the whole video.
            "-ss",
            timestamp,
            "-i",
            stream_url,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0 or not result.stdout:
        # The signed stream URL is ~1000 chars and ffmpeg echoes it back on
        # failure, so drop it first or it crowds out the actual error.
        stderr = result.stderr.decode("utf-8", "replace").replace(
            stream_url, "<stream>"
        )
        raise RuntimeError(f"ffmpeg could not extract a frame: {stderr.strip()[-500:]}")
    return result.stdout


# ── MCP tools ─────────────────────────────────────────────────────────────────


@mcp.tool(
    name="youtube_get_transcript",
    # Keep this short: it is copied into the client's context on tool discovery.
    # Details belong in the README, not here.
    description=(
        "Return a YouTube transcript as Markdown with video metadata. "
        "include_timestamps=true prefixes each line with [MM:SS]."
    ),
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
    include_timestamps: bool = False,
) -> str:
    """Return transcript Markdown for a YouTube URL or video ID."""
    url = url.strip().strip("<>")
    try:
        video_id = _extract_video_id(url)
    except ValueError as e:
        return str(e)
    langs = DEFAULT_LANGUAGES

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
                "  - Captions exist but could not be fetched"
            )
        _save_cache(video_id, langs, transcript_info)
        was_cached = False
        cached_at = ""

    metadata = _get_metadata(video_id)

    return _build_limited_output(
        metadata,
        transcript_info["entries"],
        transcript_info,
        video_id,
        include_timestamps=include_timestamps,
        cached=was_cached,
        cached_at=cached_at,
    )


@mcp.tool(
    name="youtube_get_video_info",
    description="Return YouTube video metadata as JSON without fetching subtitles.",
    annotations=ToolAnnotations(
        title="Get YouTube Video Info",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def youtube_get_video_info(url: str) -> str:
    """Return video metadata JSON for a YouTube URL or video ID."""
    url = url.strip().strip("<>")
    try:
        video_id = _extract_video_id(url)
    except ValueError as e:
        return str(e)
    metadata = _get_metadata(video_id, full_description=True)
    return json.dumps(metadata, ensure_ascii=False, indent=2)


@mcp.tool(
    name="youtube_get_frame",
    # Keep this short: it is copied into the client's context on tool discovery.
    description=(
        "Return one video frame at a timestamp as an image. "
        "timestamp accepts seconds or MM:SS / HH:MM:SS. "
        "Use when the transcript alone is not enough (slides, code, charts)."
    ),
    annotations=ToolAnnotations(
        title="Get YouTube Frame",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    # Image has no pydantic schema, so an output schema cannot be built for the
    # Image | str return that keeps errors as text like the other tools.
    structured_output=False,
)
async def youtube_get_frame(url: str, timestamp: str) -> Image | str:
    """Return a single JPEG frame from a YouTube video at the given timestamp."""
    url = url.strip().strip("<>")
    try:
        video_id = _extract_video_id(url)
    except ValueError as e:
        return str(e)

    timestamp = timestamp.strip()
    # Validated, not just parsed: an unchecked value starting with "-" would be
    # read by ffmpeg as an option rather than a timestamp.
    if not TIMESTAMP_RE.fullmatch(timestamp):
        return (
            f"Invalid timestamp: {timestamp!r}. "
            "Use seconds (90), MM:SS (01:30), or HH:MM:SS (00:01:30)."
        )

    try:
        return Image(
            data=_extract_frame(_stream_url(video_id), timestamp), format="jpeg"
        )
    except subprocess.TimeoutExpired:
        return f"Timed out fetching a frame for video {video_id}."
    except Exception as e:
        return (
            f"Error: Could not extract a frame for video {video_id} "
            f"at {timestamp}.\n{e}"
        )


if __name__ == "__main__":
    mcp.run()  # stdio

"""
FFmpeg utilities: extract audio (MP3) and trim video by timecode.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


async def _run_ffmpeg(cmd: list[str], timeout: int = 300) -> Tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "FFmpeg timeout"

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore")[-500:]
            logger.error("FFmpeg failed: %s", err)
            return False, err
        return True, ""
    except FileNotFoundError:
        return False, "ffmpeg not found. Please install ffmpeg."
    except Exception as e:
        logger.exception("FFmpeg unexpected error")
        return False, str(e)


async def extract_audio(
    input_path: Path,
    output_dir: Optional[Path] = None,
    bitrate: str = "192k",
) -> Tuple[bool, Optional[Path], str]:
    if not input_path.exists():
        return False, None, "Input file not found"

    output_dir = output_dir or input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}_{uuid.uuid4().hex[:8]}.mp3"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-b:a", bitrate,
        "-ar", "44100",
        "-ac", "2",
        str(output_path),
    ]

    ok, err = await _run_ffmpeg(cmd)
    if not ok:
        return False, None, err
    if not output_path.exists():
        return False, None, "Output MP3 not created"
    return True, output_path, ""


async def trim_video(
    input_path: Path,
    start: str,
    end: Optional[str] = None,
    duration: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Tuple[bool, Optional[Path], str]:
    if not input_path.exists():
        return False, None, "Input file not found"
    if not end and not duration:
        return False, None, "Either end or duration must be provided"

    output_dir = output_dir or input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}_trim_{uuid.uuid4().hex[:8]}.mp4"

    cmd = ["ffmpeg", "-y", "-ss", start, "-i", str(input_path)]
    if duration:
        cmd.extend(["-t", duration])
    elif end:
        cmd.extend(["-to", end])
    cmd.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", str(output_path)])

    ok, err = await _run_ffmpeg(cmd, timeout=600)
    if not ok:
        logger.warning("Stream copy failed, retrying with re-encode")
        cmd = ["ffmpeg", "-y", "-ss", start, "-i", str(input_path)]
        if duration:
            cmd.extend(["-t", duration])
        elif end:
            cmd.extend(["-to", end])
        cmd.extend([
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ])
        ok, err = await _run_ffmpeg(cmd, timeout=600)

    if not ok:
        return False, None, err
    if not output_path.exists():
        return False, None, "Trimmed file not created"
    return True, output_path, ""


def parse_timecode(text: str) -> Optional[str]:
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return text
    parts = text.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 2:
        m, s = parts
        return f"{m:02d}:{s:02d}"
    if len(parts) == 3:
        h, m, s = parts
        return f"{h:02d}:{m:02d}:{s:02d}"
    return None
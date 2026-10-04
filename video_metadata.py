#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
"""Read video dimensions and duration for accurate Telegram video attributes."""

import json
import math
import shutil
import struct
import subprocess


def _probe_ffmpeg(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=width,height,duration,duration_ts,time_base:stream_tags=rotate:stream_side_data=rotation:format=duration",
                "-of", "json",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        data = json.loads(result.stdout)
        stream = (data.get("streams") or [{}])[0]
        duration_candidates = [stream.get("duration")]
        duration_ts = stream.get("duration_ts")
        time_base = stream.get("time_base")
        if duration_ts not in (None, "N/A") and time_base:
            try:
                numerator, denominator = (
                    int(part) for part in str(time_base).split("/", 1)
                )
                if denominator:
                    duration_candidates.append(
                        float(duration_ts) * numerator / denominator
                    )
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        duration_candidates.append((data.get("format") or {}).get("duration"))
        duration = 0.0
        for candidate in duration_candidates:
            try:
                candidate = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(candidate) and candidate > 0:
                duration = candidate
                break

        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        rotation = (stream.get("tags") or {}).get("rotate")
        for side_data in stream.get("side_data_list") or []:
            if side_data.get("rotation") is not None:
                rotation = side_data["rotation"]
                break
        try:
            if int(round(float(rotation or 0))) % 180:
                width, height = height, width
        except (TypeError, ValueError):
            pass
        return {
            "duration": max(1, int(round(duration))) if duration > 0 else 0,
            "width": width,
            "height": height,
        }
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def _boxes(data, start, end):
    offset = start
    while offset + 8 <= end:
        size = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header_size = 16
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            return
        yield kind, offset + header_size, offset + size
        offset += size


def _child(data, parent, wanted):
    for kind, content_start, box_end in _boxes(data, *parent):
        if kind == wanted:
            return content_start, box_end
    return None


def _mp4_metadata(path):
    try:
        with open(path, "rb") as video:
            file_end = video.seek(0, 2)
            video.seek(0)
            moov = None
            while video.tell() + 8 <= file_end:
                box_start = video.tell()
                header = video.read(8)
                size, kind = struct.unpack(">I4s", header)
                header_size = 8
                if size == 1:
                    extended = video.read(8)
                    if len(extended) != 8:
                        return None
                    size = struct.unpack(">Q", extended)[0]
                    header_size = 16
                elif size == 0:
                    size = file_end - box_start
                if size < header_size or box_start + size > file_end:
                    return None
                if kind == b"moov":
                    content_size = size - header_size
                    if content_size > 64 * 1024 * 1024:
                        return None
                    moov = video.read(content_size)
                    break
                video.seek(box_start + size)
        if not moov:
            return None

        moov_range = (0, len(moov))
        mvhd = _child(moov, moov_range, b"mvhd")
        duration_seconds = 0
        if mvhd:
            start, end = mvhd
            version = moov[start]
            if version == 1 and start + 32 <= end:
                timescale = struct.unpack_from(">I", moov, start + 20)[0]
                ticks = struct.unpack_from(">Q", moov, start + 24)[0]
            elif start + 20 <= end:
                timescale = struct.unpack_from(">I", moov, start + 12)[0]
                ticks = struct.unpack_from(">I", moov, start + 16)[0]
            else:
                timescale, ticks = 0, 0
            if timescale:
                duration_seconds = max(1, int(round(ticks / timescale)))

        width = height = 0
        for kind, trak_start, trak_end in _boxes(moov, *moov_range):
            if kind != b"trak":
                continue
            trak_range = (trak_start, trak_end)
            tkhd = _child(moov, trak_range, b"tkhd")
            mdia = _child(moov, trak_range, b"mdia")
            is_video = False
            if mdia:
                hdlr = _child(moov, mdia, b"hdlr")
                if hdlr and hdlr[0] + 12 <= hdlr[1]:
                    is_video = moov[hdlr[0] + 8:hdlr[0] + 12] == b"vide"
            if tkhd and tkhd[1] - tkhd[0] >= 8:
                box_width, box_height = struct.unpack_from(">II", moov, tkhd[1] - 8)
                box_width >>= 16
                box_height >>= 16
                if is_video or not width:
                    width, height = box_width, box_height
                if is_video:
                    break
        if duration_seconds or width or height:
            return {
                "duration": duration_seconds,
                "width": width,
                "height": height,
            }
    except (OSError, ValueError, struct.error):
        return None
    return None


def get_video_metadata(path):
    """Return Telegram-ready duration (seconds), width, and height."""
    probed = _probe_ffmpeg(path) or {}
    fallback = _mp4_metadata(path) or {}
    return {
        "duration": probed.get("duration") or fallback.get("duration") or 0,
        "width": probed.get("width") or fallback.get("width") or 0,
        "height": probed.get("height") or fallback.get("height") or 0,
    }
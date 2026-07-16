# teslausb-viewer/app/rangeserve.py
"""Serve a local file with HTTP Range support (bytes Range -> 206 partial content).

Video playback needs seek, which requires Range requests. Starlette's FileResponse doesn't
implement Range, so this is a small, self-contained replacement for the old proxy-based
video_response() in the now-deleted stream.py.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

CHUNK_SIZE = 1024 * 1024


def serve_file_range(path: Path, range_header: str | None) -> Response:
    if not path.is_file():
        raise HTTPException(404, "clip not found")
    size = path.stat().st_size
    if not range_header or not range_header.startswith("bytes="):
        def _full():
            with open(path, "rb") as fh:
                while chunk := fh.read(CHUNK_SIZE):
                    yield chunk

        return StreamingResponse(
            _full(), status_code=200,
            headers={"content-type": "video/mp4", "accept-ranges": "bytes",
                     "content-length": str(size)},
        )

    start_s, _, end_s = range_header[len("bytes="):].partition("-")
    try:
        if not start_s and end_s:
            # Suffix range: bytes=-N means the last N bytes of the resource.
            suffix_length = int(end_s)
            start = max(0, size - suffix_length)
            end = size - 1
        else:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
    except ValueError:
        raise HTTPException(416, "invalid range")
    end = min(end, size - 1)
    if start > end or start < 0:
        raise HTTPException(416, "invalid range")
    length = end - start + 1

    def _ranged():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _ranged(), status_code=206,
        headers={
            "content-type": "video/mp4",
            "accept-ranges": "bytes",
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(length),
        },
    )

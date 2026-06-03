#!/usr/bin/env python3
"""Minimal fake `ffmpeg` for the test suite.

The thumbnailer invokes: ffmpeg -nostdin -y -ss N -i SRC -frames:v 1 -vf scale=W:-2 DEST
Real frame extraction needs a decodable video; the test backend uses random bytes, so this
shim just writes a tiny valid-enough PNG to the output path (the last argument) and exits 0,
exercising the thumbnailer's orchestration (pick clip -> fetch -> extract -> cache -> serve)
without a real codec. Put this directory first on PATH.
"""
import os
import sys

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def main(argv):
    if not argv:
        return 1
    dest = argv[-1]  # output file is the final positional argument
    try:
        with open(dest, "wb") as fh:
            fh.write(PNG)
    except OSError as exc:
        sys.stderr.write(f"fake ffmpeg: cannot write {dest}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

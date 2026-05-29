#!/usr/bin/env python3
"""Minimal fake `rclone` used by the test suite.

Maps `remote:path` to `$TUV_FAKE_BACKEND/path` and implements just the subcommands the
app uses: lsd, lsjson (+ --dirs-only/--files-only), cat, copy (+ --include), about.
Put this directory first on PATH to exercise the backend layer without a real cloud.
"""
import glob
import json
import os
import shutil
import sys

BACKEND = os.environ["TUV_FAKE_BACKEND"]


def resolve(target: str) -> str:
    _, _, path = target.partition(":")
    return os.path.join(BACKEND, path.strip("/"))


def main(argv):
    args = argv[:]
    if args and args[0] == "--config":
        args = args[2:]
    cmd, rest = args[0], args[1:]

    if cmd == "lsd":
        return 0 if os.path.isdir(resolve(rest[0])) else 3

    if cmd == "lsjson":
        d = resolve(rest[0])
        dirs_only, files_only = "--dirs-only" in rest, "--files-only" in rest
        out = []
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                full = os.path.join(d, name)
                is_dir = os.path.isdir(full)
                if (dirs_only and not is_dir) or (files_only and is_dir):
                    continue
                out.append({"Name": name, "IsDir": is_dir,
                            "Size": -1 if is_dir else os.path.getsize(full)})
        sys.stdout.write(json.dumps(out))
        return 0

    if cmd == "cat":
        p = resolve(rest[-1])
        if not os.path.isfile(p):
            sys.stderr.write("directory not found")
            return 3
        sys.stdout.buffer.write(open(p, "rb").read())
        return 0

    if cmd == "copy":
        src, dest = resolve(rest[0]), rest[1]
        includes = [rest[i + 1] for i, a in enumerate(rest) if a == "--include"]
        os.makedirs(dest, exist_ok=True)
        for pat in includes:
            for f in glob.glob(os.path.join(src, pat)):
                shutil.copy(f, os.path.join(dest, os.path.basename(f)))
        return 0

    if cmd == "about":
        sys.stdout.write(json.dumps({"total": 1000, "used": 400, "free": 600}))
        return 0

    sys.stderr.write(f"fake rclone: unknown command {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

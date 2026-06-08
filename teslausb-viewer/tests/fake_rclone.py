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
        recursive = "-R" in rest
        dirs_only, files_only = "--dirs-only" in rest, "--files-only" in rest
        pathargs = [a for a in rest if not a.startswith("-")]
        d = resolve(pathargs[0])
        out = []
        if os.path.isdir(d) and recursive:
            for root, dirnames, fnames in os.walk(d):
                rel_root = os.path.relpath(root, d)

                def _rel(name):
                    return name if rel_root == "." else os.path.normpath(os.path.join(rel_root, name))

                if not files_only:
                    for name in sorted(dirnames):
                        out.append({"Name": name, "Path": _rel(name), "IsDir": True, "Size": -1})
                if not dirs_only:
                    for name in sorted(fnames):
                        full = os.path.join(root, name)
                        out.append({"Name": name, "Path": _rel(name), "IsDir": False,
                                    "Size": os.path.getsize(full)})
        elif os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                full = os.path.join(d, name)
                is_dir = os.path.isdir(full)
                if (dirs_only and not is_dir) or (files_only and is_dir):
                    continue
                out.append({"Name": name, "Path": name, "IsDir": is_dir,
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

    if cmd == "copyto":
        src, dest = resolve(rest[0]), rest[1]
        if not os.path.isfile(src):
            sys.stderr.write("directory not found")
            return 3
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        shutil.copy(src, dest)
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

    if cmd == "serve" and rest[:1] == ["http"]:
        import http.server
        import socketserver
        from urllib.parse import unquote

        serve_args = rest[1:]
        addr, target = "127.0.0.1:8080", None
        i = 0
        valued = {"--addr", "--vfs-cache-mode", "--vfs-cache-max-size",
                  "--vfs-cache-max-age", "--cache-dir"}
        while i < len(serve_args):
            a = serve_args[i]
            if a == "--addr":
                addr = serve_args[i + 1]; i += 2; continue
            if a in valued:
                i += 2; continue
            if a.startswith("--"):
                i += 1; continue
            target = a; i += 1
        docroot = resolve(target)
        host, _, port = addr.partition(":")

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = os.path.normpath(os.path.join(docroot, unquote(self.path.lstrip("/"))))
                if not path.startswith(docroot) or not os.path.isfile(path):
                    self.send_error(404); return
                size = os.path.getsize(path)
                rng = self.headers.get("Range")
                with open(path, "rb") as fh:
                    if rng and rng.startswith("bytes="):
                        start_s, _, end_s = rng[len("bytes="):].partition("-")
                        start = int(start_s or 0)
                        end = int(end_s) if end_s else size - 1
                        end = min(end, size - 1)
                        fh.seek(start)
                        chunk = fh.read(end - start + 1)
                        self.send_response(206)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        self.send_header("Content-Length", str(len(chunk)))
                        self.end_headers()
                        self.wfile.write(chunk)
                    else:
                        data = fh.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)

        class Srv(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        with Srv((host, int(port)), H) as srv:
            srv.serve_forever()
        return 0

    sys.stderr.write(f"fake rclone: unknown command {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

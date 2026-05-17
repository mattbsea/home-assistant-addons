#!/usr/bin/env bash
# Rebuilds the PAI Observatory (Next.js) dashboard with a base path so it can
# be served correctly behind the Home Assistant ingress proxy.
#
# Usage: build-dashboard.sh <observability-dir> <base-path>
#   <base-path> may be empty, in which case a plain (root) build is produced.

set -euo pipefail

OBS="$1"
BP="$2"

cd "${OBS}"

# Pin the build configuration. basePath/assetPrefix make Next.js emit the
# ingress-prefixed URLs for every asset, route and RSC payload.
cat > next.config.ts <<'EOF'
import type { NextConfig } from "next";

const BP = process.env.PAI_BASE_PATH || "";

const nextConfig: NextConfig = {
  output: "export",
  distDir: "out",
  basePath: BP || undefined,
  assetPrefix: BP || undefined,
  images: { unoptimized: true },
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
  generateBuildId: () => "pai-addon",
};

export default nextConfig;
EOF

# basePath does not touch hand-written fetch("/api/...") calls — patch them.
if [ -n "${BP}" ]; then
    bun /opt/pai/patch-fetch.js "${OBS}/src" "${BP}"
fi

export PAI_BASE_PATH="${BP}"
bun install --frozen-lockfile
bun run build

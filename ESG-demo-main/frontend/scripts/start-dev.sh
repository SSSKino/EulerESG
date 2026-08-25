#!/bin/sh

set -eu

app_dir=$(pwd -P)
dev_dist_dir=.next-dev
cache_dir="${app_dir}/${dev_dist_dir}"

# This script is allowed to invalidate only the generated development cache.
# Refuse an unexpected path before performing any recursive cleanup.
if [ "$app_dir" != "/app" ] || [ "$cache_dir" != "/app/.next-dev" ]; then
    echo "Refusing to manage unexpected Next.js cache path: ${cache_dir}" >&2
    exit 2
fi

expected_lock=$(sha256sum package-lock.json | awk '{print $1}')
installed_lock=$(cat node_modules/.euleresg-package-lock.sha256 2>/dev/null || true)

if [ ! -d node_modules ] || [ -z "$(ls -A node_modules 2>/dev/null)" ] || [ "$expected_lock" != "$installed_lock" ]; then
    sh /usr/local/bin/npm-ci-with-registry-fallback
fi

mkdir -p "$cache_dir"
cache_signature=$(
    {
        sha256sum package.json
        sha256sum package-lock.json
        sha256sum next.config.js
        sha256sum scripts/start-dev.sh
        node --version
    } | sha256sum | awk '{print $1}'
)
# Next may replace files inside its distDir during startup. Keep the signature
# in the separately persisted dependency volume so a healthy cache survives a
# normal frontend restart.
cache_marker="${app_dir}/node_modules/.euleresg-next-dev-cache-signature"
stored_signature=$(cat "$cache_marker" 2>/dev/null || true)

if [ "$stored_signature" != "$cache_signature" ]; then
    echo "[frontend-dev] Dependency/config signature changed; rebuilding generated Next.js cache."
    find "$cache_dir" -mindepth 1 -depth -delete
    printf '%s\n' "$cache_signature" > "$cache_marker"
fi

exec npm run dev -- -H 0.0.0.0

#!/bin/sh
# Writes the build commit (first 7 chars of $GIT_COMMIT) as JSON to $1.
set -eu

out=${1:?usage: write-version.sh <output-path>}
commit=${GIT_COMMIT:-}
if [ -z "$commit" ]; then
  commit=unknown
fi
commit=$(printf '%s' "$commit" | cut -c1-7)

printf '{"commit":"%s"}\n' "$commit" > "$out"
chmod 644 "$out"

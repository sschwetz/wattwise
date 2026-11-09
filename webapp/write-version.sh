#!/bin/sh
# Writes the build commit ($GIT_COMMIT trimmed and lowercased, first 7 chars; unknown unless 7 hex chars, or dev) as JSON to $1.
set -eu

out=${1:?usage: write-version.sh <output-path>}
commit=$(printf '%s' "${GIT_COMMIT:-}" | tr 'A-Z' 'a-z' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | cut -c1-7)
case $commit in
  unknown | dev) ;;
  *)
    if ! printf '%s' "$commit" | grep -Eq '^[0-9a-f]{7}$'; then
      commit=unknown
    fi
    ;;
esac

printf '{"commit":"%s"}\n' "$commit" > "$out"
chmod 644 "$out"

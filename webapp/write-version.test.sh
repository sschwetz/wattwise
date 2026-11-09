#!/bin/sh
# Run with: sh webapp/write-version.test.sh
set -eu

here=$(cd "$(dirname "$0")" && pwd)
out=$(mktemp)
trap 'rm -f "$out"' EXIT
fail=0

check() {
  name=$1
  expected=$2
  actual=$(cat "$out")
  if [ "$actual" = "$expected" ]; then
    echo "ok   $name"
  else
    echo "FAIL $name: expected $expected, got $actual"
    fail=1
  fi
}

GIT_COMMIT=0123456789abcdef sh "$here/write-version.sh" "$out"
check "truncates to 7 chars" '{"commit":"0123456"}'

env -u GIT_COMMIT sh "$here/write-version.sh" "$out"
check "unset gives unknown" '{"commit":"unknown"}'

GIT_COMMIT= sh "$here/write-version.sh" "$out"
check "empty gives unknown" '{"commit":"unknown"}'

exit "$fail"

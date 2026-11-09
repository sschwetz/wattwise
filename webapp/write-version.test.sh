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

GIT_COMMIT=ABCDEF0123 sh "$here/write-version.sh" "$out"
check "uppercase hex is lowercased" '{"commit":"abcdef0"}'

GIT_COMMIT=HEAD sh "$here/write-version.sh" "$out"
check "HEAD gives unknown" '{"commit":"unknown"}'

GIT_COMMIT='ab"cdef0123' sh "$here/write-version.sh" "$out"
check "quote gives unknown" '{"commit":"unknown"}'

GIT_COMMIT=abc12 sh "$here/write-version.sh" "$out"
check "fewer than 7 chars gives unknown" '{"commit":"unknown"}'

GIT_COMMIT=dev sh "$here/write-version.sh" "$out"
check "dev is kept" '{"commit":"dev"}'

exit "$fail"

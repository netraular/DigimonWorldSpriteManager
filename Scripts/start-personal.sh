#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

mkdir -p -- var/tmp
input_dir="var/tmp/personal-console.$$"
mkdir -m 700 -- "$input_dir"
cleanup() {
    exec 3>&-
    rm -f -- "$input_dir/console"
    rmdir -- "$input_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkfifo -- "$input_dir/console"

# Keep input open across restarts without a background writer.
exec 3<> "$input_dir/console"
printf 'online autosync off\n' >&3
./main "$@" <&3

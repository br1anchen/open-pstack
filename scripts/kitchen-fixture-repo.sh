#!/usr/bin/env bash
set -euo pipefail

dir=${1:-}
if [ ! -d "$dir" ]; then
  echo "kitchen-fixture-repo: not a directory: $dir" >&2
  exit 1
fi

repo=$(mktemp -d)
cp -a "$dir"/. "$repo"/
git -C "$repo" init -q
git -C "$repo" add -A
git -C "$repo" -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m fixture
echo "$repo"

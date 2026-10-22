#!/usr/bin/env bash
# Trio store and Herdr channel helper for the pstack-trio skill: a shim into
# pstack-pair's pair.py; the trio's commands and configuration are in scripts/variant.py.
exec python3 -I "$(dirname "$(readlink -f "$0")")/../../pstack-pair/scripts/pair.py" --skill-root "$(dirname "$(dirname "$(readlink -f "$0")")")" "$@"

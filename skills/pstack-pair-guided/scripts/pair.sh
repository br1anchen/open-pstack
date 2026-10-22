#!/usr/bin/env bash
# Pair store and Herdr channel helper for the pstack-pair-guided skill: a shim into
# pstack-pair's pair.py; the guided commands and configuration are in scripts/variant.py.
exec python3 -I "$(dirname "$(readlink -f "$0")")/../../pstack-pair/scripts/pair.py" --skill-root "$(dirname "$(dirname "$(readlink -f "$0")")")" "$@"

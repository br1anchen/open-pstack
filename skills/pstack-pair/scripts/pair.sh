#!/usr/bin/env bash
# Pair store and Herdr channel helper for the pstack-pair skill: a shim into pair.py,
# which reads the skill's configuration and commands from pstack_core and scripts/variant.py.
exec python3 -I "$(dirname "$(readlink -f "$0")")/pair.py" --skill-root "$(dirname "$(dirname "$(readlink -f "$0")")")" "$@"

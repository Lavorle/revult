#!/usr/bin/env bash
# Convenience wrapper → scripts/run_huangmeic_playtest.sh
exec "$(cd "$(dirname "$0")" && pwd)/scripts/run_huangmeic_playtest.sh" "$@"

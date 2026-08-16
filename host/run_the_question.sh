#!/usr/bin/env bash
# Convenience wrapper → scripts/run_the_question.sh
exec "$(cd "$(dirname "$0")" && pwd)/scripts/run_the_question.sh" "$@"

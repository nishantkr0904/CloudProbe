#!/usr/bin/env bash
# CloudProbe container entrypoint (project-structure §10, architecture §11.2).
#
# Selects a run mode from CLOUDPROBE_MODE (oneshot|scheduler) and execs the CLI.
# The final `exec` matters: it replaces this shell with the Python process so
# tini's forwarded SIGTERM reaches the interpreter directly and `docker stop`
# is a clean, prompt shutdown rather than a 10-second kill.
#
# No configuration path is hardcoded: CLOUDPROBE_CONFIG points at the mounted
# config (default /etc/cloudprobe/configs) and can be overridden at run time.
# Extra arguments passed to `docker run` are forwarded to the CLI verbatim.
set -euo pipefail

MODE="${CLOUDPROBE_MODE:-oneshot}"
CONFIG="${CLOUDPROBE_CONFIG:-/etc/cloudprobe/configs}"

case "${MODE}" in
  oneshot)
    exec python -m cloudprobe run --once --config "${CONFIG}" "$@"
    ;;
  scheduler)
    exec python -m cloudprobe run --scheduler --config "${CONFIG}" "$@"
    ;;
  *)
    echo "entrypoint: unknown CLOUDPROBE_MODE '${MODE}' (expected 'oneshot' or 'scheduler')" >&2
    exit 64
    ;;
esac

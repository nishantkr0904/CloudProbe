#!/usr/bin/env bash
# CloudProbe container entrypoint (project-structure §10, architecture §11.2).
#
# Two invocation shapes, both documented:
#
#   1. No arguments — the container selects its own action from
#      CLOUDPROBE_MODE (oneshot|scheduler) and supplies CLOUDPROBE_CONFIG.
#      This is the form architecture §11.2 shows:
#          docker run -e CLOUDPROBE_MODE=oneshot ... cloudprobe
#
#   2. Arguments supplied — they are an explicit CLI invocation and are
#      forwarded verbatim to `python -m cloudprobe`, reaching the root
#      argument parser.  This is the form project-structure §16 and the
#      ROADMAP smoke test require:
#          docker run --rm cloudprobe --version
#      and it is what makes the CLI's documented subcommand surface
#      (run, healthcheck, config validate) reachable through the image
#      rather than only through an entrypoint override.
#
# Explicit arguments win over CLOUDPROBE_MODE, matching Docker's own
# convention that a command after the image name overrides the default.
#
# The final `exec` matters in both paths: it replaces this shell with the
# Python process so tini's forwarded SIGTERM reaches the interpreter
# directly and `docker stop` is a clean, prompt shutdown rather than a
# 10-second kill.
#
# No configuration path is hardcoded: CLOUDPROBE_CONFIG points at the
# mounted config (default /etc/cloudprobe/configs) and can be overridden
# at run time.
set -euo pipefail

MODE="${CLOUDPROBE_MODE:-oneshot}"
CONFIG="${CLOUDPROBE_CONFIG:-/etc/cloudprobe/configs}"

# Shape 2: an explicit command was given; do not second-guess it.
if [ "$#" -gt 0 ]; then
  exec python -m cloudprobe "$@"
fi

# Shape 1: no command; CLOUDPROBE_MODE selects the default action.
case "${MODE}" in
  oneshot)
    exec python -m cloudprobe run --once --config "${CONFIG}"
    ;;
  scheduler)
    exec python -m cloudprobe run --scheduler --config "${CONFIG}"
    ;;
  *)
    echo "entrypoint: unknown CLOUDPROBE_MODE '${MODE}' (expected 'oneshot' or 'scheduler')" >&2
    exit 64
    ;;
esac

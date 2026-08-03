# syntax=docker/dockerfile:1

# CloudProbe container image (ROADMAP Phase 6, project-structure §10).
#
# Multi-stage: the builder installs runtime dependencies into a virtualenv; the
# runtime stage copies only that venv plus the application source onto a slim
# base.  The build toolchain never reaches the final image, keeping it small and
# its attack surface minimal (architecture §13.5).
#
# Pinned by digest-free tag deliberately: the tag tracks patched 3.11-slim while
# requirements.txt pins the Python dependencies, so builds are reproducible at
# the dependency level without freezing a base image that stops receiving CVEs.

# ---------------------------------------------------------------------------
# Stage 1 — builder: create a self-contained venv with runtime deps only.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# Fail fast, no stdout buffering, no .pyc in the layer, no pip version chatter.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Only runtime requirements — requirements-dev.txt is never copied, so pytest,
# ruff, mypy and moto stay out of the image entirely.
COPY requirements.txt /tmp/requirements.txt

RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    # The installer itself is not a runtime dependency.  Dropping pip,
    # setuptools and wheel from the venv removes ~28 MB and leaves no package
    # manager in the runtime image for an attacker to install with.
    && pip uninstall --yes pip setuptools wheel 2>/dev/null || true \
    && rm -rf "$VIRTUAL_ENV"/lib/python3.11/site-packages/pip \
              "$VIRTUAL_ENV"/lib/python3.11/site-packages/pip-* \
              "$VIRTUAL_ENV"/lib/python3.11/site-packages/setuptools \
              "$VIRTUAL_ENV"/lib/python3.11/site-packages/setuptools-* \
              "$VIRTUAL_ENV"/lib/python3.11/site-packages/pkg_resources \
              "$VIRTUAL_ENV"/lib/python3.11/site-packages/wheel \
              "$VIRTUAL_ENV"/lib/python3.11/site-packages/wheel-* \
    && find "$VIRTUAL_ENV" -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && find "$VIRTUAL_ENV" -name '*.pyc' -delete

# ---------------------------------------------------------------------------
# Stage 2 — runtime: slim base, non-root, venv + source only.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    CLOUDPROBE_MODE=oneshot \
    CLOUDPROBE_CONFIG=/etc/cloudprobe/configs

# Runtime OS packages:
#   - bash: the entrypoint is a bash script (architecture §13.5).
#   - iputils-ping: the ICMP probe shells out to `ping` (probes/icmp.py); a
#     documented probe type would silently fail without it.
#   - tini: a real PID 1 that reaps zombies and forwards signals, so
#     `docker stop` reaches the Python process cleanly.
# The apt lists are removed in the same layer so they add no image weight.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash iputils-ping tini \
    && rm -rf /var/lib/apt/lists/*

# UID/GID 10001, no login shell, no home-dir writes — the fixed non-root
# identity the architecture (§13.5) and ROADMAP (Docker strategy) mandate.
RUN groupadd --gid 10001 cloudprobe \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin cloudprobe

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# The image packages src/ and the entrypoint only — never tests, docs or the
# dev toolchain (.dockerignore enforces this; project-structure §10 requires it).
COPY src/ /app/src/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# The documented mount points (architecture §11.2): config in read-only,
# reports and logs out.  Created and owned by the runtime user so a read-only
# root filesystem still permits writes to exactly these volumes.
RUN mkdir -p /etc/cloudprobe/configs /var/cloudprobe/reports /var/cloudprobe/logs \
    && chown -R 10001:10001 /var/cloudprobe

USER 10001:10001

VOLUME ["/etc/cloudprobe/configs", "/var/cloudprobe/reports", "/var/cloudprobe/logs"]

# Distinguishes "container up" from "pipeline healthy" (ROADMAP Phase 6): the
# healthcheck loads and validates the mounted config.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-m", "cloudprobe", "healthcheck", "--config", "/etc/cloudprobe/configs"]

# tini as PID 1 forwards SIGTERM/SIGINT to the entrypoint, which execs the CLI.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]

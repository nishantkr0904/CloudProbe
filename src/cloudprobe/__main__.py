"""``python -m cloudprobe`` entry point.

Delegates to :func:`cloudprobe.cli.main` so the module invocation the
architecture (§11.2) and Docker entrypoint depend on resolves.
"""

from __future__ import annotations

from cloudprobe.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

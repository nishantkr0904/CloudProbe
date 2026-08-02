"""CloudProbe report renderers — public surface.

Each module here renders the shared ``Report`` model into one output format
(project-structure §6.7, architecture §9.2).  Renderers are pure functions of
the report: they aggregate nothing, write no file, open no socket and touch no
AWS.  The HTML renderer is the first; JSON and CSV are later commits that sit
beside it without changing the ``Report`` they consume.

Callers import the renderer functions from here, never from the internal
modules.
"""

from cloudprobe.reporting.renderers.html import render_html

__all__ = [
    "render_html",
]

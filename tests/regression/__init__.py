"""Regression tests for the CloudProbe networking surface.

Each test here pins a user-visible output — a report, rendered HTML, or a
serialized AWS payload — so that the shape of what CloudProbe emits cannot
change silently.  Behavioural-stability tests (SSH executor, scheduler)
pin the externally observable contract of components the architecture gives
no golden surface (architecture §10.2).
"""

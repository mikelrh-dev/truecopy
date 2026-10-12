"""Single-worker query queue: heap order, 45s deadline, Spanish position/timeout.

Also hosts the read-only corpus watchdog (``watchdog.py``): the interval-
throttled probe that tells the operator when the served corpus is dead instead
of letting every question turn into the exact refusal.
"""

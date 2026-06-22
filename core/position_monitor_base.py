"""
core/position_monitor_base.py — tiny shared module for the
core/position_*.py mixin split (see core/position_monitor.py's
module docstring for the full explanation of why SourceState is
split across files this way).

Exists ONLY to break a circular import: core/position_monitor.py
combines the position_geometry/entry/helpers/protection/recovery
mixins into the real SourceState class, so none of those mixin files
can import anything FROM core/position_monitor.py without creating a
cycle. Anything genuinely shared (not broker/calculation logic, just
small constants/helpers) lives here instead, and every file —
including core/position_monitor.py itself — imports it from here.
"""
import logging

log = logging.getLogger("monitor_v2")

ACTIVATION_GRACE_SEC = 5


def _save(state):
    """Save session state — imported lazily to avoid circular import
    with core.resume (which itself imports SourceState for type use)."""
    try:
        from core.resume import save_session
        save_session(state)
    except Exception:
        pass

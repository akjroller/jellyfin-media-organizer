"""Plan-first Jellyfin show organizer with an explicitly gated executor."""

__version__ = "0.3.1"

from .api import AuditSummary, inspect_audit, plan_library

__all__ = ["AuditSummary", "__version__", "inspect_audit", "plan_library"]

"""Thin bridge for the existing Playwright/CDP BrowserManager.

This module intentionally knows no project-specific paths and does not alter
the legacy collector.  It only obtains an already-created BrowserContext and
attaches the passive :class:`NetworkInspector`.
"""

from __future__ import annotations

from typing import Any, Optional

from .inspector import NetworkInspector


def attach_inspector(
    context_or_manager: Any = None,
    *,
    context: Any = None,
    inspector: Optional[NetworkInspector] = None,
    module_hint: Optional[str] = None,
    **inspector_options: Any,
) -> NetworkInspector:
    """Attach to a passed context or an existing manager's ``_context``.

    The helper never creates a browser, reads a profile, or changes the current
    page.  ``context`` wins when both arguments are provided.
    """

    target = context if context is not None else context_or_manager
    if target is not None and not hasattr(target, "on"):
        target = getattr(target, "_context", None)
    if target is None or not callable(getattr(target, "on", None)):
        raise ValueError("An existing Playwright BrowserContext is required.")
    attached = inspector or NetworkInspector(**inspector_options)
    attached.attach(target, module_hint=module_hint)
    return attached


def attach_to_browser_manager(manager: Any, **kwargs: Any) -> NetworkInspector:
    """Named alias retained for old BrowserManager integrations."""

    return attach_inspector(manager, **kwargs)

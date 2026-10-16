"""G04 — focus and input routing between chrome / document / editor.

The legacy ``TabBrowser`` bound ``<Tab>`` and ``<Return>`` on the root
window; when the address entry had focus those events *also* fell
through to the page's focus chain (the report: "键盘与鼠标行为不一致",
Enter on the address bar accidentally activated a page button).  This
module centralises the routing decision.

:class:`FocusManager` is created once per :class:`TabBrowser` and is
the single entry point for keyboard routing.  It inspects the Tk
widget that currently has keyboard focus (``root.focus_get()``) and
dispatches to one of four owners:

* ``"chrome"``  — the address entry or a toolbar button (navigate).
* ``"document"`` — the page viewport (Tab / Shift+Tab / Return on
  the page's focus chain).
* ``"editor"``   — a native edit overlay (text commands; not present
  yet in the Tk transition — routes to the document owner until G03
  lands).
* ``"dialog"``   — a modal dialog (Tab restricted to the dialog's
  widgets; returns ``"break"`` for page events).

The manager is pure logic: it owns no Tk state, so it can be unit
tested without a display.
"""

from __future__ import annotations


class FocusManager:
    """Owns the four-way keyboard routing decision (G04).

    :param owner_of_widget: callback mapping a Tk widget (or its path
        name) to one of ``"chrome"`` / ``"document"`` / ``"editor"`` /
        ``"dialog"`` / ``None``.  ``None`` means "unknown — treat as
        the document owner for page events".
    """

    def __init__(self, owner_of_widget) -> None:
        self._owner_of_widget = owner_of_widget

    # ------------------------------------------------------------------
    def owner(self, widget) -> str:
        """Classify *widget* into a focus owner (G04 step 1)."""
        result = self._owner_of_widget(widget)
        if result in ("chrome", "document", "editor", "dialog"):
            return result
        return "document"

    # ------------------------------------------------------------------
    # key-event routing
    # ------------------------------------------------------------------
    def route_tab(self, owner: str, focus_next, forward: bool) -> str:
        """G04 step 3 — Tab / Shift+Tab routing.

        * ``chrome``  → break (the browser keeps the Tk tab order).
        * ``editor``  → break (the native widget handles Tab internally).
        * ``dialog``  → break (Tab is restricted inside the dialog).
        * ``document`` → *focus_next*(forward); returns "continue" so
          the caller knows the event was consumed.
        """
        if owner == "document":
            focus_next(forward)
            return "continue"
        return "break"

    def route_return(self, owner: str, activate_focused,
                     navigate_url: str = "") -> str:
        """G04 step 3 — Return / Enter routing.

        * ``chrome`` (address entry)  → *navigate_url* is the text the
          entry's Return submits (the browser navigates to it, NOT
          activates the page focus chain); returns "continue".  The
          caller performs the navigation; the manager only routes.
        * ``document``  → *activate_focused*(); returns "continue".
        * ``editor`` / ``dialog`` → "break".
        """
        if owner == "chrome":
            # The entry's Return submits *navigate_url*; the page's
            # focus chain is intentionally NOT activated.
            del navigate_url
            return "continue"
        if owner == "document":
            activate_focused()
            return "continue"
        return "break"

    # ------------------------------------------------------------------
    # focus re-establishment
    # ------------------------------------------------------------------
    @staticmethod
    def clamp_focus(page, idx: int | None) -> int | None:
        """G04 step 2 — a deleted / hidden / disabled node must not
        keep owning the focus.  *page* is a :class:`PageInstance`;
        *idx* is a DOM node index (element-only, 1-based).  Returns the
        same index when it is still in the page's :attr:`focus_chain`,
        otherwise ``None`` (blur).
        """
        if idx is None:
            return None
        chain = getattr(page, "focus_chain", None) or []
        if idx in chain:
            return idx
        return None

"""C01 — Document lifecycle and task versioning (skeleton).

Assessments 20261016-round5, batch 1 P0 item C01: keep the document's
identity stable across restate / scroll / resize / hover / focus so
that stale work products (a frame rendered for a navigation that has
since been replaced, a result belonging to a closed tab) are dropped
instead of applied.

This module is the *skeleton*: it owns the four version counters the
rest of the batch builds on, and nothing else.  The full
``DocumentState`` extraction (DOM tree + Host + DocumentPolicy +
subresource registry in one object, see C01 step 1 of the
implementation doc) is a batch-2 refactor; C01 here is the minimum
that makes G07/G08's task discarding testable *now*.

Version counters (C01 step 3 of the doc):

* ``document_id``      — 1:1 with the loaded document.  Bumped by a
  *new document load* (``load_url`` / ``load_html``).  A stale frame
  whose ``document_id`` does not match the live one is dropped.
* ``navigation_epoch`` — bumped by every *navigation request*
  (successful or not, including about: pages and error pages) *and* by
  a new document.  The difference from ``document_id``: an error page
  for a failed navigation is a *new epoch* but the "document" being
  viewed is the error page, which is what ``document_id`` names.
* ``layout_revision``  — bumped whenever layout is re-solved
  (restates for :hover/:focus, scroll re-clips, viewport resizes).
  Used to discard layout work that started before the latest restate.
* ``frame_id``         — bumped on every rasterised frame; the frame
  handle carries it so the presenter can drop superseded frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocumentVersion:
    """Monotonic version counters for one document (C01 step 3).

    All counters are process-unique (module-level sequence) so two
    documents loaded in sequence never collide; the identity of a
    result is the tuple ``(document_id, navigation_epoch,
    layout_revision, frame_id)``.
    """

    document_id: int = 0
    navigation_epoch: int = 0
    layout_revision: int = 0
    frame_id: int = 0

    # ------------------------------------------------------------------
    # mutation entry points (the only ways a counter advances)
    # ------------------------------------------------------------------
    def new_document(self) -> None:
        """C01 — a new document is loaded (parse + script init).

        Bumps *both* ``document_id`` and ``navigation_epoch``: the old
        document's frames and tasks are all stale.  A restate (hover /
        focus / scroll) does NOT call this — it calls
        :meth:`bump_layout`, which keeps the document identity.
        """
        self.document_id = _next_document_id()
        self.navigation_epoch = _next_navigation_epoch()
        self.layout_revision = _next_layout_revision()
        self.frame_id = _next_frame_id()

    def new_navigation(self) -> None:
        """C01 — a navigation request started (may fail → error page).

        Bumps ``navigation_epoch`` only: the *document* being viewed is
        about to change (or the error page will be the new document).
        """
        self.navigation_epoch = _next_navigation_epoch()

    def bump_layout(self) -> None:
        """C01 — layout was re-solved (restate / scroll / resize).

        Bumps ``layout_revision`` only: the document is the same, its
        content (DOM / CSS) is the same, but the geometry was
        recomputed.  Frames from earlier revisions must be dropped.
        """
        self.layout_revision = _next_layout_revision()

    def bump_frame(self) -> None:
        """C01 — a rasterised frame was produced.

        Bumps ``frame_id`` only.
        """
        self.frame_id = _next_frame_id()

    # ------------------------------------------------------------------
    # staleness checks (used by presenters / task queues)
    # ------------------------------------------------------------------
    def is_stale_frame(self, frame_document_id: int,
                       frame_id: int) -> bool:
        """C01 — a rendered frame is stale when its document or frame
        id is older than the live one."""
        return (frame_document_id != self.document_id
                or frame_id < self.frame_id)

    def is_stale_layout(self, layout_revision: int) -> bool:
        return layout_revision < self.layout_revision

    def identity(self) -> tuple:
        return (self.document_id, self.navigation_epoch,
                self.layout_revision, self.frame_id)


# ----------------------------------------------------------------------
# module-level monotonic sequences (process-unique, C01 step 3)
# ----------------------------------------------------------------------
_doc_seq = [0]
_nav_seq = [0]
_layout_seq = [0]
_frame_seq = [0]


def _next_document_id() -> int:
    _doc_seq[0] += 1
    return _doc_seq[0]


def _next_navigation_epoch() -> int:
    _nav_seq[0] += 1
    return _nav_seq[0]


def _next_layout_revision() -> int:
    _layout_seq[0] += 1
    return _layout_seq[0]


def _next_frame_id() -> int:
    _frame_seq[0] += 1
    return _frame_seq[0]


def reset_version_sequences() -> None:
    """Test hook — reset the module sequences to 1.. so version
    assertions are deterministic.  Call at the start of a test module
    that inspects the *values* of the counters (not just their
    monotonicity)."""
    _doc_seq[0] = 0
    _nav_seq[0] = 0
    _layout_seq[0] = 0
    _frame_seq[0] = 0

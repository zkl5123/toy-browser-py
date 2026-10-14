# Contributing to toy-browser-py

Thanks for your interest in contributing! This is a toy browser engine
written in pure Python (standard library only) that mirrors the
architecture of a real browser pipeline:

```
HTML → DOM parse → CSS cascade → Layout → Paint → Rasterize → Bitmap
```

## Getting started

1. Clone the repo.
2. Run the test suite: `python test.py` (925 tests, all pure stdlib).
3. Read `README.md` for the module map, `docs/ARCHITECTURE.md` for
   pipeline details, and `docs/ROADMAP.md` for the priority list.

## Code style

- **Pure stdlib** — no third-party dependencies. If a feature needs
  a library, implement it in pure Python instead.
- **Type hints** on all public functions (use `from __future__ import
  annotations` at the top of each module).
- **Docstrings** — every public function gets a one-line summary;
  complex algorithms get a longer explanation.
- **Memory governance** — process peak heap must stay under 6 GB
  (`mem_guard.MEMORY_CAP_MB = 6000`); per-face bitmap cache is capped
  at 12 MB (`MAX_FACE_MB`). Never allocate unbounded buffers.

## Testing

The full suite is orchestrated by `test.py`. Test modules and shared helpers
live in `tests/`; stage benchmarks live in `tools/benchmarks/`.
Individual test scripts also resolve project imports and fixtures from their new locations.
Run the full suite:

```bash
python test.py
```

A passing run prints `NNN/NNN passed` on the last line.

When adding a feature:

1. Add functional tests for the new behaviour.
2. Add a regression test for any bug fix.
3. Update the test count in `README.md` and `docs/ROADMAP.md`.

## Commit conventions

- Use imperative-mood commit messages (`Add`, `Fix`, `Refactor`).
- Prefix with the milestone tag when relevant (`P33.P1: ...`).
- Keep commits small; one logical change per commit.

## Milestone tags

The project follows a milestone scheme (P33 = project 33):

| Tag | Scope |
|-----|-------|
| R1–R5 | Roadmap milestones (completed) |
| P1  | Browser-grade gaps: var()/calc(), event model, forms, Web APIs |
| P2  | CORS/CSP, Canvas 2D, perf benchmarks |
| P3  | @font-face, SVG/media placeholders, project metadata |

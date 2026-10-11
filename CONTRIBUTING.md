# Contributing to toy-browser-py

Thanks for your interest in contributing! This is a toy browser engine
written in pure Python (standard library only) that mirrors the
architecture of a real browser pipeline:

```
HTML → DOM parse → CSS cascade → Layout → Paint → Rasterize → Bitmap
```

## Getting started

1. Clone the repo.
2. Run the test suite: `python test.py` (807+ tests, all pure stdlib).
3. Read `README.md` for the module map, `ARCHITECTURE.md` for
   pipeline details, and `ROADMAP.md` for the priority list.

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

All tests live in `test.py` (the main suite) and the `test_r*.py` /
`test_p*.py` / `test_web_api.py` files (per-milestone feature tests).
Run the full suite:

```bash
python test.py
```

A passing run prints `NNN/NNN passed` on the last line.

When adding a feature:

1. Add functional tests for the new behaviour.
2. Add a regression test for any bug fix.
3. Update the test count in `README.md` and `ROADMAP.md`.

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

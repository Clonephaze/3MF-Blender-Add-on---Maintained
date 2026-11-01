# Contributing

Thanks for your interest in contributing to **Blender 3MF Format**.

This addon provides comprehensive **3MF Core Spec v1.4.0** import/export for Blender 5.0+ (minimum Blender 4.2+), with multi-material paint support for Orca Slicer, BambuStudio, PrusaSlicer, and SuperSlicer. It's a complete modernization of the original add-on (created by Ghostkeeper) with a focus on **correctness, spec compliance, and predictable import/export behavior**.

## Where development happens

Development and issue tracking happens in this repository.

Historical upstream (no longer maintained):

- https://github.com/Ghostkeeper/Blender3mfFormat

## What to contribute

Good contributions include:

- Fixes for Blender 4.2+ / 5.0+ API changes / regressions
- 3MF import/export correctness fixes (especially edge cases)
- Test coverage (unit or integration)
- Documentation improvements (README, API docs, copilot instructions)
- MMU Paint Suite improvements (paint panel, texture handling)
- Small quality-of-life improvements that don't change file semantics
- Triangle Sets / Materials Extension support improvements

Non-goals (usually):

- "Opinionated" transformations of scene data on import/export
- Silent data loss or auto-fixing invalid files without clearly reporting it
- Large refactors without a clear benefit or test coverage
- Breaking changes to the public API (`api.py`) without strong justification

## Bug reports

Before opening a new issue, please search existing issues first.

When reporting a bug, include:

1. **Blender version** (e.g. 4.2.x LTS / 5.0.x - primary dev version is 5.0)
2. **OS** (Windows/macOS/Linux) and relevant hardware notes
3. **Steps to reproduce** (a minimal, reliable recipe)
4. **Expected vs actual behavior**
5. **Logs / traceback** (copy/paste from the system console, accessible via Window → Toggle System Console)
6. If relevant, a **minimal .3mf sample file**
7. For paint/segmentation issues: screenshots of the texture or face colors

If your file contains sensitive data, don't post it publicly. Instead, try to reproduce the issue with a sanitized/minimal file.

## Feature requests

Feature requests are welcome, but please keep the scope aligned with the project:

- The add-on's purpose is to **load and save 3MF files**.
- Changes that modify geometry/materials beyond what the format requires should be kept minimal.

When requesting a feature, describe:

- The user problem being solved
- Why it belongs in the add-on (vs being handled elsewhere)
- Any references to the 3MF spec or examples from other tools

## Pull requests

Pull requests are welcome.

### Basic workflow

1. Fork the repo
2. Create a feature branch
3. Make your changes
4. Run tests (see below)
5. Open a PR with a clear description and screenshots/files when relevant

### Testing requirements

This project has **unit tests** and **integration tests**. All tests run in **real Blender headless mode** — no mocking. They require Blender's Python interpreter.

GitHub Actions runs the code-style check and both test suites on every push and pull request.
The test matrix uses Ubuntu 24.04 with the latest patch releases of Blender 4.5 LTS, Blender 5.2 LTS,
and the latest stable Blender release. Blender downloads are cached by exact version; the latest
stable release may coincide with one of the LTS entries.

#### Run all tests (recommended before submitting PR)

Windows (PowerShell):

```powershell
python tests/run_all_tests.py
```

This spawns separate Blender processes for unit and integration tests, reporting pass/fail for both suites.
To use a particular Blender installation, pass `--blender <path>` or set the `BLENDER_EXE`
environment variable.

#### Unit tests only

Tests for individual functions (colors, segmentation codec, XML parsing, units, etc.) without creating Blender objects:

```powershell
blender --background --factory-startup --python-exit-code 1 -noaudio -q --python tests/run_unit_tests.py
```

Located in `tests/unit/`

#### Integration tests only

End-to-end tests that create real Blender objects, import/export `.3mf` files, and validate materials/geometry:

```powershell
blender --background --factory-startup --python-exit-code 1 -noaudio -q --python tests/run_tests.py
```

Located in `tests/integration/`

Test resources (sample files) are in `tests/resources/` and `tests/resources/3mf_consortium/`

**Note:** The individual test suites require Blender's Python. The master runner uses system Python
to launch Blender, which must be on your PATH unless `--blender` or `BLENDER_EXE` is set.

### Code style and architecture

- Follow [Blender's Python style guide](https://wiki.blender.org/wiki/Style_Guide/Python)
- PEP-8 compatible (`<pep8 compliant>` in headers)
- Keep changes focused and readable
- Prefer adding tests for bug fixes and behavior changes

**Architecture notes:**

- **Context dataclasses** — `ImportContext` / `ExportContext` replace mutable operator state. All helpers take `ctx` as first arg.
- **NO `logging` module** — use `common.logging` (`debug()`, `warn()`, `error()`) exclusively. Python's `logging` does nothing in Blender.
- **NO `print()` calls** — use `debug()` for dev output, `warn()`/`error()` for real issues.
- **Cache Blender strings** before XML ops — Python may GC the C string behind `blender_object.name`
- **Blender properties can't start with `_`** — use `3mf_` prefix for custom properties
- **Sub-package imports** — use `from ..common import ...` for common utilities
- See `.github/copilot-instructions.md` for full architecture documentation

### Commit messages

Write meaningful commit messages. Avoid generic messages like “Update file” or “Fix stuff”.

Good examples:

- `Fix material slot indexing when exporting empty slots`
- `Update import operator for Blender 4.2 depsgraph API`

### Changelog

Please don't update `CHANGELOG.md` unless you're asked to. Maintainers will handle release notes.

## Reviewing PRs is also contributing

If you're not ready to code, you can still help by:

- Trying the add-on on your Blender version and reporting results
- Testing PR branches
- Improving docs
- Sharing minimal repro files for tricky import/export bugs

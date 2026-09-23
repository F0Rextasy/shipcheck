---
name: shipcheck
description: Verifies release artifacts before they reach an index. Use after building wheels/sdists and before any publish step -- reopens every archive, reinstalls wheels into a throwaway dir, imports them, compares filename versions against METADATA, and checks console scripts point at shipped modules, so a broken artifact fails here instead of on a user's machine.
license: MIT
compatibility: Requires Python 3.8+. Runs in Claude Code, Codex, Cursor, and any Agent Skills compatible client.
metadata:
  author: F0Rextasy
  version: "1.0"
---

# shipcheck

pip installing an artifact proves nothing: an empty wheel installs fine, a
renamed file keeps its old METADATA, a console script can point at a module
that was never shipped. Those failures surface on user machines, days
later, as one-star issues. shipcheck moves them left - into the release
pipeline, before the upload.

## The one rule

You may not publish `dist/` until the gate is green:

```bash
python -m build
python scripts/shipcheck.py dist/ --src .
```

Exit 0 means every artifact opens, installs, imports, and agrees with its
own metadata. Exit 1 means something in `dist/` is broken: rebuild, fix, or
justify exactly one rule with `--allow RULE=reason`.

## Protocol

1. **Build** - `python -m build` (or your builder) into `dist/`.
2. **Gate** - `python scripts/shipcheck.py dist/ --src . --strict` in CI.
   The install step uses `--no-deps --no-index`: no network, hermetic,
   ~seconds per wheel.
3. **Fix or allow** - a finding is either a rebuild or a documented
   exception:

```bash
python scripts/shipcheck.py dist/ --allow unimportable="needs GPU at runtime"
```

Exemptions print as `(1 exempt by --allow)` in every summary - visible in
the log, auditable later.

## Rules at a glance

- `empty-package` (fail) - archive ships no Python modules; the classic
  misconfigured `packages/find` backend.
- `unimportable` (fail) - pip installs it, `import` fails. The failure your
  users would have filed.
- `metadata-mismatch` (fail) - filename version disagrees with METADATA /
  PKG-INFO; a hand-renamed artifact.
- `broken-entrypoint` (fail) - a console script points at a module that is
  not in the archive.
- `stale-artifact` (warn) - source tree is newer than `dist/`; you forgot
  to rebuild.
- `no-dist` / `unreadable-dist` (warn) - nothing to gate, or an archive
  that cannot even be opened.

Full catalogue: [references/RULES.md](references/RULES.md).

## Scope

Wheels get the full gate (open, reinstall, import, metadata, entry
points). Sdists get structural checks (open, module presence, PKG-INFO
version) - importing an sdist would require building it, which is the
builder's job, not the gate's.

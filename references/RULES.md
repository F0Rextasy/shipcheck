# Rule catalogue

`scripts/shipcheck.py` reopens release artifacts and verifies them the way
a user would: install, import, run the entry point's module lookup, read
the metadata. Every rule answers one question: *will this artifact work on
a machine that is not mine?*

Two severities:

- **fail** - the artifact is broken as shipped. Fails the gate (exit 1).
- **warn** - the artifact may be fine, but the release process looks wrong.
  Fails only with `--strict`.

## Fail rules

| Rule | Fires when | Example |
| --- | --- | --- |
| `empty-package` | the archive contains no `.py` modules | src-layout backend with no `packages` configured |
| `unimportable` | pip installs the wheel, `import <top>` fails in a clean interpreter | undeclared dependency, guarded import at module scope |
| `metadata-mismatch` | filename version differs from METADATA / PKG-INFO `Version` | `mypkg-0.2.0-*.whl` with `Version: 0.1.0` inside |
| `broken-entrypoint` | a `console_scripts`/`gui_scripts` target module is absent from the archive | `foo = mypkg.missing:main` with no `missing.py` shipped |

## Warn rules

| Rule | Fires when | Why it is not fatal |
| --- | --- | --- |
| `stale-artifact` | a `.py` under `--src` is newer than the artifact | you may release from a tag; usually you forgot to rebuild |
| `no-dist` | no `.whl` / `.tar.gz` under the path | a gate on an unbuilt tree must not crash |
| `unreadable-dist` | the archive cannot be opened, or pip cannot install it | pip itself would fail; visibility only |

## Method

- **Wheels**: unzip (METADATA, entry points, top-level modules), then
  `pip install --no-deps --no-index --target <tmp>` and `import <top>` per
  top-level package in a subprocess with `PYTHONPATH=<tmp>`. No network,
  no site-packages pollution, no build backend involved.
- **Sdists**: structural only (open, module list, PKG-INFO version vs
  filename). Installing an sdist requires a build; that is the builder's
  responsibility, verified here by the presence of real modules.
- **Top-level detection**: every `.py` outside `*.dist-info` contributes
  its first path segment (`pkg/__init__.py` -> `pkg`, `mod.py` -> `mod`).
- **Findings point at line 1** - the artifact. Archives have no lines;
  `code` carries the basename for grep.

## Escape hatch

Binary artifacts cannot hold comments, so exemptions travel on the
command line and land in the log:

```bash
python scripts/shipcheck.py dist/ --allow unimportable="needs GPU at runtime"
```

`--allow RULE=reason`, repeatable. Unknown rules and missing reasons are
usage errors (exit 2) - a typo must not silently pass. Every suppression
counts as `suppressed` in text and JSON.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | clean, or only warnings without `--strict` |
| `1` | at least one fail (or any warning with `--strict`) |
| `2` | usage error - path missing, bad `--allow`, bad flags |

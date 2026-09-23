# shipcheck

**A pip-installable artifact can still be broken.** shipcheck verifies
release artifacts *before* they reach an index: it reopens every wheel and
sdist under `dist/`, reinstalls wheels into a throwaway dir, imports them,
and compares filename versions against their own metadata. One command per
gate - run it after every build.

## Why this exists

pip installing an artifact proves nothing: an empty wheel installs fine, a
renamed file keeps its old METADATA, a console script can point at a module
that was never shipped. Those failures surface on user machines, days
later, as one-star issues. shipcheck is a four-part quality-gate family
for agent and human work:

| Gate | Catches |
| --- | --- |
| **preflight** | `prod` in debug, `example.com` URLs, wildcard CORS, flat `requirements` |
| **prove-it** | claims (`all tests pass`) with no executed command + exit code behind them |
| **testgate** | tests that can never fail - the green that proves nothing |
| shipcheck: verifies release artifacts before they reach an index *(this repo)* |

Copies the family contract: one Python script, zero dependencies, exit 0 =
clean, 1 = broken artifact, 2 = usage error, `--strict` also fails
warnings.

## Install

Single script, stdlib only (Python 3.8+). Vendor it or run in place:

```bash
cp scripts/shipcheck.py your-repo/scripts/
python -m build
python scripts/shipcheck.py dist/ --src .
```

Or as an Agent Skill:

```
/shipcheck  # in Claude Code, Codex, Cursor, or any Agent Skills client
```

## Usage

```console
$ python scripts/shipcheck.py dist/ --src .
dist/empty-0.1.0-py3-none-any.whl
  L1    FAIL  empty-package      wheel contains no Python modules

shipcheck: 1 failure, 0 warnings across 2 artifacts (0 exempt by --allow)
shipcheck: rebuild, fix, or pass --allow RULE=reason
[exit 1]
```

Point it at a `dist/` dir or a single artifact. The install step uses
`pip install --no-deps --no-index --target <tmpdir>`: no network,
hermetic, ~seconds per wheel. `--format json` emits machine-readable
findings; `--strict` also fails warnings. Binary artifacts cannot hold
comments, so exemptions travel on the command line and land in the log:

```bash
python scripts/shipcheck.py dist/ --allow unimportable="needs GPU at runtime"
```

## The rules

| Rule | Severity | Finds |
| --- | --- | --- |
| `empty-package` | fail | archive ships no Python modules |
| `unimportable` | fail | pip installs it, `import` fails |
| `metadata-mismatch` | fail | filename version disagrees with METADATA / PKG-INFO |
| `broken-entrypoint` | fail | console script points at a module not in the archive |
| `stale-artifact` | warn | source tree newer than `dist/` - you forgot to rebuild |
| `no-dist` / `unreadable-dist` | warn | nothing to gate, or an unopenable archive |

Full catalogue with method notes: [references/RULES.md](references/RULES.md).

Wheels get the full gate (open, reinstall, import, metadata, entry
points). Sdists get structural checks - installing one requires a build,
which is the builder's job; the gate verifies real modules are inside.

## Evidence (real outputs)

Broken demo dist (empty wheel + mislabeled version, built with stdlib):

```console
$ python scripts/shipcheck.py examples/demo-dist-broken --src examples/src
examples/demo-dist-broken\emptything-0.1.0-py3-none-any.whl
  L1    FAIL  empty-package      wheel contains no Python modules
examples/demo-dist-broken\mislabeled-0.2.0-py3-none-any.whl
  L1    FAIL  metadata-mismatch  filename says 0.2.0, METADATA says 0.1.0
shipcheck: 2 failures, 0 warnings across 2 artifacts (0 exempt by --allow)
shipcheck: rebuild, fix, or pass --allow RULE=reason
[exit 1]
```

Clean dist (built by `examples/make_fixtures.py`):

```console
$ python examples/make_fixtures.py && python scripts/shipcheck.py examples/dist --src examples/src
built fixtures in .../examples/dist
shipcheck: clean -- 2 artifacts checked, 0 findings (0 exempt by --allow)
[exit 0]
```

Contract tests, 9 for 9:

```console
$ python -m unittest discover -s tests -v
.........
----------------------------------------------------------------------
Ran 9 tests in 11.039s

OK
[exit 0]
```

Every test builds real wheels/sdists with the stdlib, installs real wheels
with pip, and asserts the observable exit code and findings.

## Layout

```text
shipcheck/
+-- scripts/shipcheck.py    # the gate (stdlib only, ~380 lines)
+-- SKILL.md                # Agent Skill (Claude Code / Codex / Cursor)
+-- examples/make_fixtures.py # deterministic fixture builder (stdlib)
+-- references/RULES.md     # rule catalogue, method, escape hatch
+-- tests/test_shipcheck.py # contract tests building real artifacts
```

(`examples/dist` is built, not committed - see `.gitignore`.)

## License

[MIT](LICENSE)

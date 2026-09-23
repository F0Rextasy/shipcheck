#!/usr/bin/env python3
"""shipcheck -- verify release artifacts before they reach an index.

pip installing an artifact proves nothing: an empty wheel installs fine,
a renamed file keeps its old METADATA, a console script can point at a
module that was never shipped. shipcheck opens every wheel and sdist under
dist/, reinstalls wheels into a throwaway target dir, and imports them --
so a broken artifact fails here instead of on a user's machine.

Exit codes: 0 clean, 1 findings fail (warnings only with --strict),
2 usage.
"""
from __future__ import annotations

import json

import argparse
import configparser
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from email.parser import Parser

RULES = {
    # rule: (severity, suggestion)
    "empty-package": ("fail",
        "fix the build backend config (packages/find) so modules are "
        "included, then rebuild"),
    "unimportable": ("fail",
        "make the top-level package importable with no extra steps -- "
        "declare the dependency or vendor it"),
    "metadata-mismatch": ("fail",
        "rebuild from a clean tree; never rename a built artifact by hand"),
    "broken-entrypoint": ("fail",
        "ship the module the console script points at, or drop the script"),
    "no-dist": ("warn",
        "build first (python -m build), then gate dist/"),
    "stale-artifact": ("warn",
        "rebuild so dist/ matches the source tree, then re-run the gate"),
    "unreadable-dist": ("warn",
        "the artifact cannot even be opened -- rebuild it"),
}

WHEEL_RE = re.compile(r"^(?P<name>.+?)-(?P<ver>[^-]+?)(-[^-]+?)?"
                      r"-py\d.*-none-any\.whl$")
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", "venv",
             ".venv", "build", "dist", "site-packages", ".tox", ".eggs",
             "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ENTRY_SECTIONS = ("console_scripts", "gui_scripts")


@dataclass
class Finding:
    file: str
    line: int
    rule: str
    code: str
    message: str

    @property
    def severity(self) -> str:
        return RULES[self.rule][0]


def last_line(text, limit=160):
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1][:limit] if lines else "(no output)"


def find_artifacts(path):
    if os.path.isfile(path):
        return [path]
    out = []
    for name in sorted(os.listdir(path)):
        if name.endswith((".whl", ".tar.gz")):
            out.append(os.path.join(path, name))
    return out


def read_dist_info_meta(zf, names):
    for name in names:
        if name.endswith(".dist-info/METADATA"):
            return zf.read(name).decode("utf-8", "replace")
    return None


def top_levels(names):
    tops = set()
    for p in names:
        head = p.split("/", 1)[0]
        if head.endswith((".dist-info", ".data")):
            continue
        if p.endswith(".py"):
            tops.add(p[:-3] if "/" not in p else head)
    return sorted(tops)


def entry_targets(text):
    """Yield (script, module) pairs from an entry_points.txt body."""
    cfg = configparser.ConfigParser()
    try:
        cfg.read_string(text)
    except configparser.Error:
        return
    for section in ENTRY_SECTIONS:
        if not cfg.has_section(section):
            continue
        for script, ref in cfg.items(section):
            yield script, ref.split(":")[0].strip()


def check_wheel(path, src_newest):
    findings = []
    base = os.path.basename(path)
    try:
        zf = zipfile.ZipFile(path)
        names = zf.namelist()
    except (zipfile.BadZipFile, OSError) as exc:
        return [Finding(path, 1, "unreadable-dist", base,
                        "archive cannot be opened: %s" % str(exc)[:80])]

    nameset = set(names)
    py_files = [n for n in names if n.endswith(".py")
                and not n.split("/", 1)[0].endswith((".dist-info", ".data"))]
    if not py_files:
        findings.append(Finding(path, 1, "empty-package", base,
                                "wheel contains no Python modules"))
        return findings

    meta_text = read_dist_info_meta(zf, names)
    meta_ver = None
    if meta_text is not None:
        meta_ver = Parser().parsestr(meta_text).get("Version")
    m = WHEEL_RE.match(base)
    file_ver = m.group("ver") if m else None
    if meta_ver and file_ver and meta_ver.strip() != file_ver:
        findings.append(Finding(
            path, 1, "metadata-mismatch", base,
            "filename says %s, METADATA says %s" % (file_ver, meta_ver)))

    ep_name = next((n for n in names if n.endswith("/entry_points.txt")), None)
    if ep_name is not None:
        for script, mod in entry_targets(
                zf.read(ep_name).decode("utf-8", "replace")):
            rel = mod.replace(".", "/")
            if rel + ".py" not in nameset and rel + "/__init__.py" not in nameset:
                findings.append(Finding(
                    path, 1, "broken-entrypoint", base,
                    "console script %r points at missing module %r"
                    % (script, mod)))

    with tempfile.TemporaryDirectory(prefix="shipcheck-") as target:
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps",
             "--no-index", "--target", target, path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        if install.returncode != 0:
            findings.append(Finding(
                path, 1, "unreadable-dist", base,
                "pip could not install it: %s" % last_line(install.stderr)))
            return findings
        for top in top_levels(names):
            env = dict(os.environ, PYTHONPATH=target)
            imp = subprocess.run(
                [sys.executable, "-c", "import %s" % top],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=120)
            if imp.returncode != 0:
                findings.append(Finding(
                    path, 1, "unimportable", base,
                    "import %s failed: %s" % (top, last_line(imp.stderr))))

    if src_newest is not None:
        try:
            if os.path.getmtime(path) < src_newest[0]:
                findings.append(Finding(
                    path, 1, "stale-artifact", base,
                    "source %s is newer -- rebuild before release"
                    % src_newest[1]))
        except OSError:
            pass
    return findings


def check_sdist(path, src_newest):
    findings = []
    base = os.path.basename(path)
    try:
        tf = tarfile.open(path, "r:gz")
        names = tf.getnames()
    except (tarfile.TarError, OSError) as exc:
        return [Finding(path, 1, "unreadable-dist", base,
                        "archive cannot be opened: %s" % str(exc)[:80])]

    py_files = [n for n in names if n.endswith(".py")]
    if not py_files:
        findings.append(Finding(path, 1, "empty-package", base,
                                "sdist contains no Python modules"))
        return findings

    pkg_info = next((n for n in names if n.endswith("PKG-INFO")), None)
    if pkg_info is not None:
        member = tf.getmember(pkg_info)
        fh = tf.extractfile(member)
        ver = Parser().parsestr(
            fh.read().decode("utf-8", "replace")).get("Version") if fh else None
        core = base[:-7]  # strip .tar.gz
        if ver and not core.endswith("-" + ver.strip()):
            findings.append(Finding(
                path, 1, "metadata-mismatch", base,
                "filename %r does not end with PKG-INFO version %r"
                % (base, ver)))

    if src_newest is not None:
        try:
            if os.path.getmtime(path) < src_newest[0]:
                findings.append(Finding(
                    path, 1, "stale-artifact", base,
                    "source %s is newer -- rebuild before release"
                    % src_newest[1]))
        except OSError:
            pass
    return findings


def newest_source(src):
    """(mtime, relpath) of the newest .py under src, or None."""
    if os.path.isfile(src):
        try:
            return (os.path.getmtime(src), src)
        except OSError:
            return None
    best = None
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            full = os.path.join(root, name)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, os.path.relpath(full, src))
    return best


def parse_allow(values):
    allows = {}
    for item in values or []:
        if "=" not in item or not item.split("=", 1)[1].strip():
            raise ValueError("--allow needs RULE=reason, got %r" % item)
        rule, reason = item.split("=", 1)
        if rule not in RULES:
            raise ValueError("unknown rule in --allow: %r" % rule)
        allows[rule] = reason.strip()
    return allows


def render_text(findings, stats, strict):
    counts = {"fail": 0, "warn": 0}
    for f in findings:
        counts[f.severity] += 1

    out = []
    by_file = {}
    for f in findings:
        by_file.setdefault(f.file, []).append(f)
    for group_file, group in by_file.items():
        out.append(group_file)
        for f in group:
            sev = "FAIL" if f.severity == "fail" else "WARN"
            out.append("  L%-4d %-5s %-18s %s"
                       % (f.line, sev, f.rule, f.message))

    nf = "%d failure%s" % (counts["fail"], "" if counts["fail"] == 1 else "s")
    nw = "%d warning%s" % (counts["warn"], "" if counts["warn"] == 1 else "s")
    exempt = " (%d exempt by --allow)" % stats["suppressed"]
    nart = len(stats["artifacts"])

    if not findings:
        out.append("shipcheck: clean -- %d artifact%s checked, 0 findings%s"
                   % (nart, "" if nart == 1 else "s", exempt))
        return "\n".join(out)

    out.append("shipcheck: %s, %s across %d artifact%s%s"
               % (nf, nw, nart, "" if nart == 1 else "s", exempt))
    if counts["fail"] > 0 or (strict and counts["warn"] > 0):
        out.append("shipcheck: rebuild, fix, or pass --allow RULE=reason")
    else:
        out.append("shipcheck: warnings pass by default; use --strict to "
                   "fail on them too")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="shipcheck",
        description="Verify release artifacts: reopen every wheel/sdist, "
                    "reinstall wheels, import them, compare metadata. "
                    "Exit 1 when an artifact is broken.")
    ap.add_argument("path", nargs="?", default="dist",
                    help="dist dir or a single artifact (default: dist)")
    ap.add_argument("--src", default=".",
                    help="source tree for the stale-artifact check")
    ap.add_argument("--allow", action="append", metavar="RULE=reason",
                    help="exempt one rule with a reason (repeatable)")
    ap.add_argument("--strict", action="store_true",
                    help="also fail on warnings")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        ap.error("path not found: %s" % args.path)
    try:
        allows = parse_allow(args.allow)
    except ValueError as exc:
        ap.error(str(exc))

    artifacts = find_artifacts(args.path)
    if not artifacts:
        findings = [Finding(args.path, 1, "no-dist",
                            os.path.basename(args.path.rstrip(os.sep)) or args.path,
                            "no .whl or .tar.gz under this path")]
        stats = {"artifacts": [], "suppressed": 0}
    else:
        src_newest = newest_source(args.src)
        raw = []
        for art in artifacts:
            if art.endswith(".whl"):
                raw.extend(check_wheel(art, src_newest))
            else:
                raw.extend(check_sdist(art, src_newest))
        findings = []
        suppressed = 0
        for f in raw:
            if f.rule in allows:
                suppressed += 1
            else:
                findings.append(f)
        stats = {"artifacts": artifacts, "suppressed": suppressed}

    counts = {"fail": 0, "warn": 0}
    for f in findings:
        counts[f.severity] += 1
    failing = counts["fail"] > 0 or (args.strict and counts["warn"] > 0)

    if args.format == "json":
        payload = {
            "ok": not failing,
            "counts": dict(counts, suppressed=stats["suppressed"]),
            "scanned": {"artifacts": stats["artifacts"]},
            "findings": [
                {"file": f.file, "line": f.line, "rule": f.rule,
                 "severity": f.severity, "code": f.code,
                 "message": f.message, "suggestion": RULES[f.rule][1]}
                for f in findings
            ],
        }
        import json
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_text(findings, stats, args.strict))

    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())

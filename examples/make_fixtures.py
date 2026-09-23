"""Deterministic fixture wheels/sdists for shipcheck, stdlib only.

The wheels are real archives: pip installs them, Python imports them.
Fixed zip timestamps keep builds reproducible; file mtimes are left alone
so the stale-artifact check can distinguish fresh from stale.
"""

import base64
import hashlib
import os
import tarfile
import time
import zipfile

FIXED_DATE = (2026, 1, 1, 0, 0, 0)
FIXED_MTIME = 1767225600  # 2026-01-01 UTC


def make_wheel(dist_dir, name="mypkg", version="0.1.0", modules=None,
               entry_points=None, meta_version=None):
    if modules is None:
        modules = {"mypkg/__init__.py":
                   '__version__ = "0.1.0"\n\n\ndef add(a, b):\n    return a + b\n'}
    dist_info = "%s-%s.dist-info" % (name, version)
    files = dict(modules)
    files[dist_info + "/METADATA"] = (
        "Metadata-Version: 2.1\nName: %s\nVersion: %s\n"
        % (name, meta_version or version))
    files[dist_info + "/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: shipcheck-fixtures\n"
        "Root-Is-Purelib: true\nTag: py3-none-any\n")
    if entry_points is not None:
        files[dist_info + "/entry_points.txt"] = entry_points
    record = []
    for arc, data in files.items():
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(data.encode("utf-8")).digest()).rstrip(b"=").decode()
        record.append("%s,sha256=%s,%d" % (arc, digest, len(data.encode("utf-8"))))
    record.append(dist_info + "/RECORD,,")
    files[dist_info + "/RECORD"] = "\n".join(record) + "\n"

    os.makedirs(dist_dir, exist_ok=True)
    path = os.path.join(dist_dir, "%s-%s-py3-none-any.whl" % (name, version))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, data in files.items():
            info = zipfile.ZipInfo(arc, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return path


def make_sdist(dist_dir, name="mypkg", version="0.1.0", modules=None,
               pkg_version=None):
    if modules is None:
        modules = {"mypkg/__init__.py":
                   '__version__ = "0.1.0"\n\n\ndef add(a, b):\n    return a + b\n'}
    root = "%s-%s" % (name, version)
    os.makedirs(dist_dir, exist_ok=True)
    path = os.path.join(dist_dir, "%s.tar.gz" % root)
    pkg_info = ("Metadata-Version: 2.1\nName: %s\nVersion: %s\n"
                % (name, pkg_version or version))
    with tarfile.open(path, "w:gz") as tf:
        for arc, data in list(modules.items()) + [("PKG-INFO", pkg_info)]:
            blob = data.encode("utf-8")
            info = tarfile.TarInfo(root + "/" + arc)
            info.size = len(blob)
            info.mtime = FIXED_MTIME
            import io
            tf.addfile(info, io.BytesIO(blob))
    return path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    dist = os.path.join(here, "dist")
    src = os.path.join(here, "src")
    os.makedirs(os.path.join(src, "mypkg"), exist_ok=True)
    with open(os.path.join(src, "mypkg", "__init__.py"), "w") as fh:
        fh.write('__version__ = "0.1.0"\n')
    # src stays older than the artifacts built right after it
    make_wheel(dist)
    make_sdist(dist)
    print("built fixtures in %s" % dist)

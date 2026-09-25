"""Contract for the in-app update check: pure comparison logic and app wiring."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.core.update_check import (
    LATEST_RELEASE_URL, is_newer, latest_release, parse_version)
from mtgdb.core.version import app_version


def _version_with_stale_metadata():
    """Resolve app_version() with two bundled dist-info folders on the path.

    The in-app swap copies additively and never purges, so after an update the
    old ``mtg_deck_builder-<old>.dist-info`` sits beside the new one inside
    ``_internal``. ``importlib.metadata.version`` returns the *first* match --
    alphabetically the older version -- which made an upgraded 1.2.0 build
    report itself as 1.1.0 and re-offer the very update it had just installed.
    The resolver must return the highest version present. Versions well above
    pyproject's are used so a pyproject fallback cannot fake a pass.
    """
    import os
    import sys
    import tempfile
    root = tempfile.mkdtemp(prefix="mtg-stale-meta-")
    try:
        for value in ("9.1.0", "9.2.0"):
            folder = os.path.join(root, f"mtg_deck_builder-{value}.dist-info")
            os.makedirs(folder)
            with open(os.path.join(folder, "METADATA"), "w", encoding="utf-8") as f:
                f.write(f"Metadata-Version: 2.1\nName: mtg-deck-builder\n"
                        f"Version: {value}\n")
        sys.path.insert(0, root)
        try:
            return app_version()
        finally:
            sys.path.remove(root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def _version_with_one_corrupt_dist_info():
    """Resolve app_version() with a good folder beside a corrupt one.

    The additive swap can leave several dist-info folders bundled (see
    ``_version_with_stale_metadata``); one whose METADATA file is truncated or
    otherwise unreadable must not take the good ones down with it. Building the
    candidate list in a single comprehension let one bad ``.version`` access
    (a raised exception, not a missing/falsy value) discard every candidate,
    including the current version, falling all the way back to "0.0.0" -- both
    mis-displaying the version and making the app perpetually claim an update
    is available right after it just updated.
    """
    import os
    import sys
    import tempfile
    root = tempfile.mkdtemp(prefix="mtg-corrupt-meta-")
    try:
        good = os.path.join(root, "mtg_deck_builder-9.3.0.dist-info")
        os.makedirs(good)
        with open(os.path.join(good, "METADATA"), "w", encoding="utf-8") as f:
            f.write("Metadata-Version: 2.1\nName: mtg-deck-builder\nVersion: 9.3.0\n")
        bad = os.path.join(root, "mtg_deck_builder-9.0.0.dist-info")
        os.makedirs(bad)
        # Malformed bytes (not merely missing) so reading .version raises
        # rather than returning a falsy/None value.
        with open(os.path.join(bad, "METADATA"), "wb") as f:
            f.write(b"\x00\x01\xff\xfe not valid utf-8 \x80\x81")
        sys.path.insert(0, root)
        try:
            return app_version()
        finally:
            sys.path.remove(root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def main():
    app_source = (ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")
    updates_source = (ROOT / "mtgdb/ui/updates.py").read_text(encoding="utf-8")

    def raising_fetch(_url):
        raise RuntimeError("offline")

    checks = {
        "version parsing tolerates a leading v and missing parts": (
            parse_version("v1.0.4") == (1, 0, 4)
            and parse_version("1.2") == (1, 2, 0)
            and parse_version("garbage") is None),
        "is_newer is strict and order-correct": (
            is_newer("v1.0.5", "1.0.4")
            and is_newer("1.1.0", "1.0.9")
            and not is_newer("1.0.4", "1.0.4")
            and not is_newer("v1.0.3", "1.0.4")
            and not is_newer("garbage", "1.0.4")),
        "latest_release returns tag and page for a normal release": (
            latest_release(
                lambda _u: {"tag_name": "v1.0.5", "html_url": "https://x/rel"})
            == ("v1.0.5", "https://x/rel")),
        "latest_release is silent on error, draft, or prerelease": (
            latest_release(raising_fetch) == (None, None)
            and latest_release(lambda _u: {"tag_name": "v2", "draft": True})
            == (None, None)
            and latest_release(lambda _u: {"tag_name": "v2", "prerelease": True})
            == (None, None)),
        "release feed is an https GitHub API URL": (
            LATEST_RELEASE_URL.startswith("https://api.github.com/repos/")
            and LATEST_RELEASE_URL.endswith("/releases/latest")),
        "app composes the update mixin and triggers the check off first paint": (
            "from mtgdb.ui.updates import UpdateCheckMixin" in app_source
            and "UpdateCheckMixin" in app_source
            and "self._build_update_banner(self)" in app_source
            and "self._start_update_check()" in app_source),
        "app_version picks the newest of several bundled dist-info folders": (
            _version_with_stale_metadata() == "9.2.0"),
        "app_version survives one corrupt dist-info among several": (
            _version_with_one_corrupt_dist_info() == "9.3.0"),
        "the update banner tells the user which version they are on": (
            "you have {app_version()}" in updates_source),
        "the check runs on a background thread and never blocks the UI": (
            "spawn_daemon(worker" in updates_source
            and "from mtgdb.core.net import get_json" in updates_source
            and "latest_release(get_json)" in updates_source
            and "is_newer(tag" in updates_source
            and "webbrowser.open" in updates_source),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nUPDATE CHECK:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

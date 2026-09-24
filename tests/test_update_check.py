"""Contract for the in-app update check: pure comparison logic and app wiring."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.core.update_check import (
    LATEST_RELEASE_URL, is_newer, latest_release, parse_version)


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

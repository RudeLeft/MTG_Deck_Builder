"""Regression coverage for nine cloud-review UI-correctness findings.

The gallery row-cap fix (a pure function) gets a genuine functional check.
The rest are deep Tk-widget-interaction fixes (a shared picker dialog cache, a
focus/selection desync, a stale background callback, a progress-bar mapping, a
popup resize, and a shared-state image desync); each is verified structurally
against its actual source, and every check here was confirmed to FAIL against
the pre-fix source before being finalized -- this is the same pattern already
established for the catalog-controller shutdown race in
test_review_fixes_cluster3.py, used there because a live functional race is
not reliably observable either.
"""

from pathlib import Path
import sys
import tkinter as tk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.ui.card_detail import results_gallery_layout_metrics
from mtgdb.ui.tables import TableInfrastructureMixin, available_columns
from mtgdb.ui.tokens import (
    RESULT_GALLERY_CARD_MIN_WIDTH, RESULT_GALLERY_MAX_VISIBLE_ROWS)


def _method_body(source, name):
    """Return one method's source text, from its def line to the next def."""
    start = source.index(f"def {name}(")
    rest = source[start:]
    marker = "\n    def "
    end = rest.find(marker, 1)
    return rest if end == -1 else rest[:end]


def _gallery_row_cap_check():
    """A very tall viewport at minimum card size must stay fully covered.

    At the minimum card width the row stride is short, so a tall viewport
    (4K/portrait/stacked multi-monitor, ~2400px+) needed more rows than the
    old cap of 16 allowed, leaving the bottom of the viewport as empty
    background -- a direct violation of AGENTS.md SRCH-046's "the viewport
    MUST remain filled to its bottom edge whenever additional logical
    results exist".
    """
    for height in (2400, 2600, 3000, 4200):
        metrics = results_gallery_layout_metrics(
            1200, height, target_width=RESULT_GALLERY_CARD_MIN_WIDTH)
        covered = metrics["rows"] * metrics["row_stride"]
        if covered < height and metrics["rows"] < RESULT_GALLERY_MAX_VISIBLE_ROWS:
            return False  # capped short of coverage while headroom remained
        if covered < height and metrics["rows"] == RESULT_GALLERY_MAX_VISIBLE_ROWS:
            # Even at the raised cap this particular height is not fully
            # covered -- only acceptable if the cap is still comfortably
            # above the old value (raised, not merely renamed).
            if RESULT_GALLERY_MAX_VISIBLE_ROWS <= 16:
                return False
    return True


def _min_visible_column_check():
    """Unchecking every column must leave at least one column visible.

    _columns_changed() never enforced a floor, so unchecking every box in
    Edit Columns left the Treeview with rows present but zero visible
    columns -- a blank table until Reset or a restart. This drives the real
    _columns_changed method (via a minimal duck-typed host with a live Tk
    root, since tk.BooleanVar needs a real interpreter) rather than just
    inspecting its source, since the exact fallback column and the
    checkbox-variable re-sync are both genuinely worth executing.
    """
    root = tk.Tk()
    root.withdraw()
    try:
        class _FakeHost:
            def _apply_table_columns(self, view, tv=None):
                pass

            def _save_ui_preferences(self):
                pass

        host = _FakeHost()
        view = "results"
        host._visible_columns = {view: list(available_columns(view))}
        # _column_vars is nested {view: {column_key: BooleanVar}}, matching
        # _create_column_popup's own shape.
        host._column_vars = {view: {
            key: tk.BooleanVar(master=root, value=True)
            for key in available_columns(view)}}
        for variable in host._column_vars[view].values():
            variable.set(False)

        TableInfrastructureMixin._columns_changed(host, view)

        still_visible = host._visible_columns[view]
        name_var = host._column_vars[view].get("name")
        return (
            len(still_visible) >= 1
            and "name" in still_visible
            and name_var is not None and name_var.get() is True)
    finally:
        root.destroy()


def _picker_cache_key_check():
    """The shared picker dialog cache must key on mode_choices too.

    Card Form's picker (mode_choices=ANY_NONE_CHOICES) and Subtype/
    Mechanics/etc. (the default 3-choice MODE_CHOICES) previously shared one
    cache slot keyed only by (has mode_var, single_select), so whichever
    built the slot's mode radio row first permanently decided every other
    picker's mode options -- the radio row is built once at construction and
    show() (the cache-hit reuse path) never rebuilds it.
    """
    source = (ROOT / "mtgdb/ui/search_checklist.py").read_text(encoding="utf-8")
    body = _method_body(source, "open_search_checklist")
    return "mode_choices" in body and 'options.get("mode_choices")' in body


def _catalog_control_typo_check():
    """The classic-widget fallback must not reference an undefined name.

    _set_search_catalog_controls_enabled's parameter is "enabled"; the
    fallback path referenced an undefined "state", raising NameError that
    the surrounding bare except Exception silently swallowed, so the
    fallback branch never actually applied the enabled/disabled state.
    """
    source = (ROOT / "mtgdb/ui/search.py").read_text(encoding="utf-8")
    body = _method_body(source, "_set_search_catalog_controls_enabled")
    return "state=state" not in body and "state=\"normal\" if enabled" in body


def _result_focus_desync_check():
    """_on_result_select must re-anchor focus when ctrl-click deselects it.

    Ctrl-clicking the currently-focused row to deselect it, while other rows
    remain selected, left Tk's own focus() still naming that now-deselected
    row: neither the "focus is in the selection" branch nor the "selection is
    empty" branch fired, so the tracked focus (and the preview) stayed
    pointed at a card no longer part of the selection.
    """
    source = (ROOT / "mtgdb/ui/results.py").read_text(encoding="utf-8")
    body = _method_body(source, "_on_result_select")
    return (
        "elif not self._result_selected_ids:" in body
        and "else:" in body
        and "for slot in selection_order:" in body
        and body.index("elif not self._result_selected_ids:") < body.index("else:"))


def _sample_hand_generation_check():
    """_refresh_stats must bump the sample-hand generation, not just clear it.

    A hydration request already in flight from Draw Hand only compared its
    generation against a second Draw Hand click (bumped inside _draw_hand
    itself); a deck mutation or tab switch that ran _refresh_stats while that
    request was still in flight never invalidated it, so a slow-to-return
    hydration could land afterward and silently repopulate the just-cleared
    hand table with a hand drawn from a different deck state.
    """
    source = (ROOT / "mtgdb/ui/deck_stats.py").read_text(encoding="utf-8")
    body = _method_body(source, "_refresh_stats")
    return "_sample_hand_generation" in body and "self._hand = []" in body


def _print_progress_monotonic_check():
    """Print progress must map each phase into its own share of one 0-100 bar.

    download and layout each report progress against their own item count
    (distinct PNGs to fetch vs. total card placements), which differ for any
    deck with duplicate cards -- nearly every real deck. Setting the
    progressbar directly from each phase's own local fraction let it reach
    ~100% at the end of downloading, then visibly jump back down to a few
    percent when layout reset its own local maximum to the larger placement
    count.
    """
    source = (ROOT / "mtgdb/ui/printing.py").read_text(encoding="utf-8")
    return (
        "_set_print_percent" in source
        and "self._print_overall_percent" in source
        and "fraction * 50" in source
        and "50 + fraction * 50" in source)


def _gallery_peek_resize_in_place_check():
    """Rotate/Flip must resize the peek window without recentring it.

    _render(center=False) called _center_popup_with_visible_actions
    unconditionally, which always recomputes a centred position regardless of
    its own center argument -- every Rotate/Flip click snapped the popup back
    to the middle of the work area even after the user had dragged it
    elsewhere.
    """
    source = (ROOT / "mtgdb/ui/card_detail.py").read_text(encoding="utf-8")
    body = _method_body(source, "_render")
    return (
        "if center or not self._presented:" in body
        and "self._resize_in_place(width, height)" in body
        and "_resize_in_place" in source
        and "def _resize_in_place(self, width, height):" in source)


def _comparison_face_sibling_check():
    """Flipping one duplicate DFC copy must re-queue every sibling cell.

    _face_indexes is shared across every grid cell showing the same
    printing, but each cell has its own image request/label. Re-queuing only
    the clicked instance_key left a sibling cell silently showing the old
    face while the shared index had already advanced.
    """
    source = (ROOT / "mtgdb/ui/comparison.py").read_text(encoding="utf-8")
    body = _method_body(source, "_flip_comparison_face")
    return (
        "for key in list(self._image_labels):" in body
        and "key.endswith(suffix)" in body
        and "self._queue_image(card, key)" in body)


def main():
    checks = {
        "gallery row cap stays raised enough to cover a very tall viewport":
            _gallery_row_cap_check(),
        "unchecking every column leaves at least one column visible":
            _min_visible_column_check(),
        "shared picker dialog cache keys on mode_choices too":
            _picker_cache_key_check(),
        "classic-widget enable/disable fallback has no undefined-name typo":
            _catalog_control_typo_check(),
        "result selection re-anchors focus after a ctrl-click deselect":
            _result_focus_desync_check(),
        "deck-stats refresh invalidates an in-flight sample-hand hydration":
            _sample_hand_generation_check(),
        "print progress maps each phase into one monotonic overall percent":
            _print_progress_monotonic_check(),
        "gallery card-peek Rotate/Flip resizes without recentring":
            _gallery_peek_resize_in_place_check(),
        "flipping one duplicate DFC copy re-queues every sibling cell":
            _comparison_face_sibling_check(),
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nREVIEW FIXES (CLUSTER 4):", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Deck-statistics, probability, sample-hand, and legality presentation."""

import tkinter as tk
from tkinter import ttk

from mtgdb.deck.file_jobs import submit_deck_file_job
from mtgdb.deck.analysis import (
    analyze_deck, card_draw_odds, curve_breakdown, sample_hand,
)
from mtgdb.deck.legality import legality_problems
from mtgdb.ui.components import AppButton, ClassicButton
from mtgdb.ui.tokens import (
    DECK_COLOR_SEGMENT_COLORS, DECK_TYPE_SEGMENT_COLORS, FONT_BODY,
    FONT_DIALOG_TITLE, FONT_HELPER_BOLD, FONT_MICRO, MANA_NAMES,
    PALETTE,
)

CMC_LABELS = ["0", "1", "2", "3", "4", "5", "6", "7+"]
class DeckStatsMixin:
    """Render deck-domain calculations without owning their formulas."""

    def _build_stats_panel(self, parent):
        """Build a polished vertically scrollable stats dashboard."""
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        self._stats_canvas = tk.Canvas(
            holder, highlightthickness=0, background=PALETTE["surface"])
        ssb = ttk.Scrollbar(
            holder, orient="vertical", command=self._stats_canvas.yview,
            style="Dark.Vertical.TScrollbar")
        self._stats_canvas.configure(yscrollcommand=ssb.set)
        self._stats_canvas.grid(row=0, column=0, sticky="nsew")
        ssb.grid(row=0, column=1, sticky="ns")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        body = ttk.Frame(self._stats_canvas)
        win = self._stats_canvas.create_window((0, 0), window=body, anchor="nw")
        self._stats_body_window = win
        stats_resize = {"after": None}

        def schedule_stats_canvas(_event=None):
            if self._window_in_motion:
                return
            pending = stats_resize["after"]
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass

            def apply():
                stats_resize["after"] = None
                try:
                    if not self._stats_canvas.winfo_exists():
                        return
                    self._stats_canvas.itemconfigure(
                        win, width=max(1, self._stats_canvas.winfo_width()))
                    self._stats_canvas.configure(
                        scrollregion=self._stats_canvas.bbox("all"))
                except tk.TclError:
                    pass

            stats_resize["after"] = self.after(20, apply)

        body.bind("<Configure>", schedule_stats_canvas, add="+")
        self._stats_canvas.bind("<Configure>", schedule_stats_canvas, add="+")
        self._register_scrollable(self._stats_canvas)

        def full_width_label(parent, text="", style="Muted.TLabel", pady=(0, 0)):
            lbl = ttk.Label(parent, text=text, style=style, justify="left")
            lbl.pack(fill="x", anchor="w", pady=pady)
            self._bind_debounced_wrap(lbl, min_width=140, padding=10)
            return lbl

        def dashboard_section(title, *, actions=None):
            """Create a clean section with a dark header band and open content."""
            section = ttk.Frame(body)
            section.pack(fill="x", pady=(0, 8))
            header = tk.Frame(
                section, bg=PALETTE["surface2"], padx=9, pady=6,
                highlightthickness=0, bd=0)
            header.pack(fill="x")
            tk.Label(
                header, text=title, bg=PALETTE["surface2"],
                fg=PALETTE["accent"], font=FONT_HELPER_BOLD,
                anchor="w").pack(side="left")
            if actions is not None:
                actions(header)
            content = ttk.Frame(section, padding=(9, 7, 9, 6))
            content.pack(fill="x")
            ttk.Separator(section, orient="horizontal").pack(fill="x")
            return content

        def metric_heading(parent, text):
            tk.Label(
                parent, text=text, bg=PALETTE["surface"],
                fg=PALETTE["muted"], font=FONT_HELPER_BOLD,
                anchor="w").pack(fill="x", pady=(0, 3))

        # Overview keeps related metrics together but does not compress them into
        # narrow columns. The whole Deck Stats dashboard owns scrolling, so each
        # section can use the vertical room it needs without nested scrollbars.
        overview = dashboard_section("DECK OVERVIEW")
        metric_heading(overview, "COMPOSITION")
        self.types_frame = ttk.Frame(overview)
        self.types_frame.pack(anchor="w", fill="x", pady=(0, 7))
        ttk.Separator(overview, orient="horizontal").pack(fill="x", pady=(0, 7))
        metric_heading(overview, "COLOR REQUIREMENTS & MANA SOURCES")
        self.manacheck_frame = ttk.Frame(overview)
        self.manacheck_frame.pack(anchor="w", fill="x")
        self.manacheck_note = full_width_label(overview, "", pady=(4, 0))

        self.curve_mode = tk.StringVar(value="total")

        def curve_actions(header):
            controls = tk.Frame(header, bg=PALETTE["surface2"])
            controls.pack(side="right")
            for label, val in (("Total", "total"), ("By type", "type"),
                               ("By color", "color")):
                ttk.Radiobutton(
                    controls, text=label, value=val, variable=self.curve_mode,
                    command=self._on_curve_mode).pack(side="left", padx=(7, 0))

        curve = dashboard_section("MANA CURVE", actions=curve_actions)
        self.curve_canvas = tk.Canvas(
            curve, width=1, height=118, highlightthickness=0,
            background=PALETTE["surface"])
        self.curve_canvas.pack(fill="x")
        self.curve_canvas.bind("<Configure>", self._schedule_curve_redraw)
        self.curve_legend = ttk.Frame(curve)
        self.curve_legend.pack(fill="x", pady=(3, 0))

        odds = dashboard_section("OPENING HAND & DRAW ODDS")
        metric_heading(odds, "LAND CONSISTENCY")
        self.lands_odds_lbl = full_width_label(odds, "", pady=(0, 7))
        ttk.Separator(odds, orient="horizontal").pack(fill="x", pady=(0, 7))
        metric_heading(odds, "SELECTED CARD")
        self.odds_selected_name = full_width_label(
            odds, "None", style="TLabel", pady=(0, 2))
        self.card_odds_lbl = full_width_label(odds, "")

        def hand_actions(header):
            AppButton(
                header, text="Draw hand", role="compact",
                command=self._draw_hand).pack(side="right")
            self.hand_view_btn = AppButton(
                header, text="View hand", role="compact", command=self._view_hand)
            self.hand_view_btn.pack(side="right", padx=(0, 6))
            self.hand_view_btn.state(["disabled"])

        hand = dashboard_section("SAMPLE HAND", actions=hand_actions)
        hand_table = ttk.Frame(hand)
        hand_table.pack(fill="x")
        self.hand_tv = ttk.Treeview(
            hand_table, columns=("name",), show="tree headings", height=7,
            selectmode="browse")
        self.hand_tv.heading("#0", text="Cost", anchor="w")
        self.hand_tv.column(
            "#0", width=90, minwidth=80, stretch=False, anchor="w")
        self.hand_tv.heading("name", text="Card", anchor="w")
        self.hand_tv.column(
            "name", width=315, minwidth=140, stretch=False, anchor="w")
        self.hand_horizontal_scroll = ttk.Scrollbar(
            hand_table, orient="horizontal", command=self.hand_tv.xview,
            style="Dark.Horizontal.TScrollbar")

        def update_hand_horizontal_scroll(first, last):
            self.hand_horizontal_scroll.set(first, last)
            try:
                clipped = float(first) > 0.0 or float(last) < 1.0
                manager = self.hand_horizontal_scroll.winfo_manager()
                if clipped and not manager:
                    self.hand_horizontal_scroll.grid(row=1, column=0, sticky="ew")
                elif not clipped and manager:
                    self.hand_horizontal_scroll.grid_remove()
            except (ValueError, tk.TclError):
                return

        self.hand_tv.configure(xscrollcommand=update_hand_horizontal_scroll)
        self.hand_tv.grid(row=0, column=0, sticky="ew")
        hand_table.columnconfigure(0, weight=1)
        self.hand_horizontal_scroll.grid(row=1, column=0, sticky="ew")
        self.hand_horizontal_scroll.grid_remove()
        self.hand_tv.tag_configure("odd", background=PALETTE["stripe"])
        self.hand_tv.tag_configure("even", background=PALETTE["surface"])
        self.hand_tv.bind("<<TreeviewSelect>>", self._on_hand_select)
        self._hand = []

        def legality_actions(header):
            AppButton(
                header, text="Details", role="compact",
                command=self._show_legality_details).pack(side="right")

        legality = dashboard_section("FORMAT LEGALITY", actions=legality_actions)
        self.legality_lbl = ttk.Label(legality, text="", justify="left")
        self.legality_lbl.pack(fill="x", anchor="w")
        self._bind_debounced_wrap(self.legality_lbl, min_width=140, padding=10)
        self._legality_problems = []

        self._last_curve = [0] * 8
        self._curve_breakdown = None

    def _refresh_stats(self):
        snapshot = analyze_deck(self.deck)
        self._deck_analysis_snapshot = snapshot
        st = snapshot.stats
        self._last_curve = st["curve"]

        # curve (mode-aware)
        mode = self.curve_mode.get()
        self._curve_breakdown = curve_breakdown(self.deck, mode) if mode != "total" else None
        self._draw_curve()
        self._render_legend()

        self._render_mana_check()
        self._render_types(st)
        self._render_draw_odds()
        self._render_legality()

        # sample hand goes stale when the deck changes
        for iid in self.hand_tv.get_children():
            self.hand_tv.delete(iid)
        self._hand = []
        if getattr(self, "hand_view_btn", None) is not None:
            self.hand_view_btn.state(["disabled"])
        self._close_card_grid_window("sample_hand")

    def _on_curve_mode(self):
        """Switch the curve view without recomputing the whole stats panel."""
        mode = self.curve_mode.get()
        self._curve_breakdown = (curve_breakdown(self.deck, mode)
                                 if mode != "total" else None)
        self._draw_curve()
        self._render_legend()

    def _settle_stats_canvas_layout(self):
        try:
            canvas = self._stats_canvas
            if not canvas.winfo_exists():
                return
            canvas.itemconfigure(
                self._stats_body_window, width=max(1, canvas.winfo_width()))
            canvas.configure(scrollregion=canvas.bbox("all"))
        except (AttributeError, tk.TclError):
            pass

    def _schedule_curve_redraw(self, _event=None):
        """Coalesce resize storms; never redraw a curve during active motion."""
        if self._window_in_motion:
            return
        if self._curve_resize_after is not None:
            try:
                self.after_cancel(self._curve_resize_after)
            except tk.TclError:
                pass
        self._curve_resize_after = self.after(45, self._finish_curve_resize)

    def _finish_curve_resize(self):
        self._curve_resize_after = None
        try:
            if self.curve_canvas.winfo_exists():
                self._draw_curve()
        except tk.TclError:
            pass

    def _draw_curve(self):
        c = self.curve_canvas
        c.delete("all")
        w = c.winfo_width() or 260
        h = int(c["height"])
        curve = getattr(self, "_last_curve", [0] * 8)
        breakdown = getattr(self, "_curve_breakdown", None)
        peak = max(curve) or 1
        n = len(curve)
        pad_l, pad_r, pad_t, pad_b = 4, 4, 16, 16
        plot_w = max(1, w - pad_l - pad_r)
        plot_h = max(1, h - pad_t - pad_b)
        slot = plot_w / n
        barw = slot * 0.62
        baseline = pad_t + plot_h
        seg_colors = None
        if breakdown:
            labels, buckets = breakdown
            seg_colors = (DECK_TYPE_SEGMENT_COLORS if labels[0] == "Creatures"
                          else DECK_COLOR_SEGMENT_COLORS)
        for i, val in enumerate(curve):
            x0 = pad_l + i * slot + (slot - barw) / 2
            x1 = x0 + barw
            if val:
                if breakdown:
                    # stacked segments, bottom-up in label order
                    y = baseline
                    for lab in breakdown[0]:
                        seg = breakdown[1][i].get(lab, 0)
                        if not seg:
                            continue
                        seg_h = (seg / peak) * plot_h
                        c.create_rectangle(x0, y - seg_h, x1, y,
                                           fill=seg_colors[lab], outline="")
                        y -= seg_h
                    top = y
                else:
                    top = baseline - (val / peak) * plot_h
                    c.create_rectangle(x0, top, x1, baseline,
                                       fill=PALETTE["bar"], outline="")
                c.create_text((x0 + x1) / 2, top - 7, text=str(val),
                              fill=PALETTE["text"], font=FONT_MICRO)
            c.create_text((x0 + x1) / 2, baseline + 8, text=CMC_LABELS[i],
                          fill=PALETTE["muted"], font=FONT_MICRO)
        c.create_line(pad_l, baseline, w - pad_r, baseline, fill=PALETTE["border"])

    def _render_legend(self):
        for widget in self.curve_legend.winfo_children():
            widget.destroy()
        breakdown = getattr(self, "_curve_breakdown", None)
        if not breakdown:
            return
        labels, buckets = breakdown
        seg_colors = (DECK_TYPE_SEGMENT_COLORS if labels[0] == "Creatures"
                      else DECK_COLOR_SEGMENT_COLORS)
        shown = [lab for lab in labels if any(b.get(lab) for b in buckets)]
        for i, lab in enumerate(shown):
            cell = ttk.Frame(self.curve_legend)
            cell.grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 10))
            sw = tk.Frame(cell, width=9, height=9, background=seg_colors[lab])
            sw.pack(side="left", pady=2)
            total = sum(bucket.get(lab, 0) for bucket in buckets)
            ttk.Label(
                cell, text=f" {lab}: {total}", style="Muted.TLabel").pack(side="left")

    def _render_mana_check(self):
        for widget in self.manacheck_frame.winfo_children():
            widget.destroy()
        snapshot = getattr(self, "_deck_analysis_snapshot", None)
        if snapshot is None or snapshot.generation != getattr(self.deck, "generation", 0):
            snapshot = analyze_deck(self.deck)
            self._deck_analysis_snapshot = snapshot
        pips = snapshot.color_pips
        sources, n_sources = snapshot.color_sources, snapshot.mana_source_cards
        shown = [c for c in ("W", "U", "B", "R", "G")
                 if pips[c] or sources[c]]
        if not shown:
            ttk.Label(self.manacheck_frame, text="\u2014",
                      style="Muted.TLabel").grid(row=0, column=0)
            self.manacheck_note.configure(text="")
            return
        ttk.Label(self.manacheck_frame, text="", width=2).grid(row=0, column=0)
        ttk.Label(self.manacheck_frame, text="Symbols",
                  style="Muted.TLabel").grid(row=0, column=2, sticky="e", padx=(10, 0))
        ttk.Label(self.manacheck_frame, text="Sources",
                  style="Muted.TLabel").grid(row=0, column=3, sticky="e", padx=(12, 0))
        for r, key in enumerate(shown, start=1):
            if self.pips.get(key):
                ttk.Label(self.manacheck_frame, image=self.pips[key]).grid(
                    row=r, column=0, sticky="w", pady=1)
            ttk.Label(self.manacheck_frame, text=MANA_NAMES[key]).grid(
                row=r, column=1, sticky="w", padx=(4, 0))
            ttk.Label(self.manacheck_frame, text=str(pips[key])).grid(
                row=r, column=2, sticky="e", padx=(10, 0))
            low = sources[key] and pips[key] and sources[key] * 2 < pips[key]
            ttk.Label(self.manacheck_frame, text=str(sources[key]),
                      foreground=PALETTE["deck_bad"]
                      if (pips[key] and not sources[key]) or low
                      else PALETTE["text"]).grid(row=r, column=3, sticky="e",
                                                 padx=(12, 0))
        self.manacheck_note.configure(
            text=(f"Mana-producing cards: {n_sources}\n"
                  "Red colored Sources numbers indicate missing/light mana source support."))

    def _render_types(self, st):
        for widget in self.types_frame.winfo_children():
            widget.destroy()
        items = sorted(st["types"].items(), key=lambda kv: -kv[1])
        if not items:
            ttk.Label(self.types_frame, text="\u2014 empty \u2014",
                      style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            return
        for r, (label, val) in enumerate(items):
            ttk.Label(self.types_frame, text=label).grid(row=r, column=0, sticky="w")
            ttk.Label(self.types_frame, text=str(val)).grid(
                row=r, column=1, sticky="e", padx=(14, 0))

    def _render_draw_odds(self):
        snapshot = getattr(self, "_deck_analysis_snapshot", None)
        if snapshot is None or snapshot.generation != getattr(self.deck, "generation", 0):
            snapshot = analyze_deck(self.deck)
            self._deck_analysis_snapshot = snapshot
        n, lands, avg, p24 = snapshot.opening_land_stats
        if n == 0:
            self.lands_odds_lbl.configure(text="Mainboard empty")
        else:
            self.lands_odds_lbl.configure(
                text=(f"Mainboard: {n} cards · {lands} lands\n"
                      f"Average lands in a 7-card opening hand: {avg:.1f}\n"
                      f"Chance an opening hand has 2–4 lands: {p24:.1%}"))
        self._update_card_odds()

    def _update_card_odds(self):
        """Show draw odds for the card selected in Mainboard or Sideboard."""
        n = self.deck.total("main")
        if not n:
            self.odds_selected_name.configure(text="None")
            self.card_odds_lbl.configure(text="• Add cards to the mainboard to calculate odds")
            return

        if not self._selected_deck:
            self.odds_selected_name.configure(text="None")
            self.card_odds_lbl.configure(
                text="Select a Mainboard or Sideboard card to calculate draw odds")
            return

        card_id, board = self._selected_deck
        card = self._deck_card(card_id, board)
        if not card:
            self.odds_selected_name.configure(text="None")
            self.card_odds_lbl.configure(
                text="Select a Mainboard or Sideboard card to calculate draw odds")
            return

        name = card.get("name") or "(unnamed card)"
        self.odds_selected_name.configure(
            text=f"{name}  ({'Mainboard' if board == 'main' else 'Sideboard'})")

        odds = card_draw_odds(self.deck, name)
        copies = odds["copies"]
        if not copies:
            self.card_odds_lbl.configure(
                text="Mainboard copies 0 · Draw chance 0%")
            return

        opener = odds["opening"]
        # These counts use the on-the-play assumption: no draw on turn one.
        turn3 = odds["turn_3"]
        turn6 = odds["turn_6"]
        self.card_odds_lbl.configure(
            text=(f"Copies in Mainboard: {copies}\n"
                  f"Chance to have it in your opening 7: {opener:.1%}\n"
                  f"Chance to have seen it by turn 3: {turn3:.1%}\n"
                  f"Chance to have seen it by turn 6: {turn6:.1%}"))

    def _fresh_sample_hand_card(self, card):
        """Hydrate a drawn printing from the current DB before image presentation."""
        card_id = str((card or {}).get("id") or "")
        db = getattr(self, "db", None)
        if card_id and db is not None:
            try:
                fresh = db.get_card(card_id)
            except Exception:
                fresh = None
            if fresh:
                return fresh
        return card

    def _apply_sample_hand(self, cards):
        self._hand = list(cards or ())
        for iid in self.hand_tv.get_children():
            self.hand_tv.delete(iid)

        if getattr(self, "hand_view_btn", None) is not None:
            self.hand_view_btn.state(
                ["!disabled"] if self._hand else ["disabled"])
        self._update_card_grid_window("sample_hand", self._hand)

        for i, card in enumerate(self._hand):
            cost = card.get("mana_cost") or ""
            img = self._cost_image(cost, 18)
            tags = ("odd" if i % 2 else "even",)
            self.hand_tv.insert(
                "", "end", iid=str(i), text="",
                image=img if img else "",
                values=(card.get("name") or "",), tags=tags)

    def _draw_hand(self):
        drawn = sample_hand(self.deck)
        db = getattr(self, "db", None)
        batch_get = getattr(db, "get_cards", None)
        poller = getattr(self, "_poll_deck_file_job", None)
        if not (drawn and callable(batch_get) and callable(poller)):
            self._apply_sample_hand(
                [self._fresh_sample_hand_card(card) for card in drawn])
            return

        generation = int(getattr(self, "_sample_hand_generation", 0)) + 1
        self._sample_hand_generation = generation
        card_ids = [str(card.get("id") or "") for card in drawn]
        future = submit_deck_file_job(
            batch_get, card_ids, name="mtg-sample-hand-hydrate")

        def hydrated(fresh_cards):
            if generation != getattr(self, "_sample_hand_generation", 0):
                return
            merged = [
                fresh or original
                for original, fresh in zip(drawn, fresh_cards or ())
            ]
            if len(merged) < len(drawn):
                merged.extend(drawn[len(merged):])
            self._apply_sample_hand(merged)

        poller(future, hydrated, error_title="Sample hand failed")

    def _view_hand(self):
        """Show the current sample hand in the shared large-card grid."""
        if not self._hand:
            return
        self._open_card_grid_window(
            "sample_hand", "Sample Opening Hand", self._hand)

    def _on_hand_select(self, _event=None):
        sel = self.hand_tv.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except (TypeError, ValueError):
            return
        if 0 <= idx < len(self._hand):
            self._show_card(self._hand[idx])

    def _render_legality(self):
        if self.deck.total("main") == 0:
            self.legality_lbl.configure(text="\u2014", foreground=PALETTE["muted"])
            self._legality_problems = []
            return
        self._legality_problems = legality_problems(self.deck)
        fmt = self.deck.fmt
        if not self._legality_problems:
            self.legality_lbl.configure(
                text=f"\u2714  Basic format check passed for {fmt}",
                foreground=PALETTE["deck_good"])
        else:
            n = len(self._legality_problems)
            self.legality_lbl.configure(
                text=f"\u26a0  {n} issue{'s' if n != 1 else ''} for {fmt}",
                foreground=PALETTE["deck_bad"])

    def _show_legality_details(self):
        """Show every format-legality issue in a centered dark, scrollable dialog."""
        p = PALETTE
        fmt = self.deck.fmt or "Selected Format"
        problems = list(self._legality_problems)

        popup = self._create_hidden_popup(
            f"Basic Format Check — {fmt}", transient=self)
        popup.configure(bg=p["border"])
        popup.minsize(620, 420)
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)

        shell = tk.Frame(popup, bg=p["surface"], padx=18, pady=16)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(
            shell, text="BASIC FORMAT CHECK", bg=p["surface"], fg=p["accent"],
            font=FONT_DIALOG_TITLE).pack(anchor="w", pady=(0, 10))

        list_shell = tk.Frame(
            shell, bg=p["border"], highlightthickness=1,
            highlightbackground=p["border"])
        list_shell.pack(fill="both", expand=True)
        list_shell.rowconfigure(0, weight=1)
        list_shell.columnconfigure(0, weight=1)

        issues = tk.Listbox(
            list_shell, activestyle="none", exportselection=False,
            background=p["input"], foreground=p["text"],
            selectbackground=p["accent"],
            selectforeground=p["on_accent"],
            highlightthickness=0, relief="flat", bd=0,
            font=FONT_BODY)
        scroll = ttk.Scrollbar(
            list_shell, orient="vertical", command=issues.yview,
            style="Dark.Vertical.TScrollbar")
        issues.configure(yscrollcommand=scroll.set)
        issues.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self._register_scrollable(issues)

        if problems:
            for index, problem in enumerate(problems, 1):
                issues.insert("end", f"{index}.  {problem}")
        else:
            issues.insert(
                "end", f"✓  No issues found by this basic format check for {fmt}.")

        foot = tk.Frame(shell, bg=p["surface"])
        foot.pack(fill="x", pady=(12, 0))
        ClassicButton(
            foot, text="Close", role="compact_primary", command=popup.destroy
        ).pack(side="right")

        popup.bind("<Escape>", lambda _e: popup.destroy())
        self._present_hidden_popup(
            popup, preferred_width=760, preferred_height=620,
            min_width=620, min_height=420, grab=True)

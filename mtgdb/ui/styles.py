"""Application-wide ttk theme registration.

All ttk dimensions, typography, and state colors are registered here so the
feature UI only selects a semantic component role.
"""

from tkinter import font as tkfont, ttk

from mtgdb.ui.tokens import (
    FONT_ACTIVITY,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_DIALOG_TITLE,
    FONT_HELPER,
    FONT_HELPER_BOLD,
    FONT_MICRO,
    FONT_MICRO_BOLD,
    FORM_CONTROL_PADDING,
    PAD_COMPACT,
    PAD_DECK,
    PAD_DENSE,
    PAD_DENSE_PRIMARY,
    PAD_PICKER,
    PAD_SEARCH_PICKER,
    PAD_PRIMARY,
    PAD_STANDARD,
    PALETTE,
)


def _configure_button(style, name, *, background, foreground, font, padding,
                      borderwidth=1, bordercolor=None, relief="solid"):
    options = {
        "background": background,
        "foreground": foreground,
        "font": font,
        "padding": padding,
        "borderwidth": borderwidth,
        "relief": relief,
    }
    if bordercolor is not None:
        options.update(
            bordercolor=bordercolor, lightcolor=bordercolor, darkcolor=bordercolor)
    style.configure(name, **options)


def _map_button(style, name, *, primary=False):
    p = PALETTE
    if primary:
        style.map(
            name,
            background=[("disabled", p["surface3"]),
                        ("pressed", p["accent2"]),
                        ("alternate", p["accent2"]),
                        ("active", p["accent2"])],
            foreground=[("disabled", p["muted"]),
                        ("pressed", p["on_accent"]),
                        ("alternate", p["on_accent"]),
                        ("active", p["on_accent"])],
            bordercolor=[("pressed", p["accent2"]),
                         ("alternate", p["accent2"])],
            relief=[("pressed", "sunken")],
        )
    else:
        style.map(
            name,
            background=[("pressed", p["select"]),
                        ("alternate", p["select"]),
                        ("active", p["surface3"])],
            foreground=[("disabled", p["muted"])],
            bordercolor=[("disabled", p["border"]),
                         ("pressed", p["accent"]),
                         ("alternate", p["accent"]),
                         ("active", p["border"])],
            relief=[("pressed", "sunken")],
        )


TREEVIEW_ROW_PADDING = 6
TREEVIEW_MIN_ROW_HEIGHT = 28

# Tk's `scaling` at 100% display scaling (96 dpi).  Pixel constants in this UI are
# written for that size; anything that has to hold scaled text is multiplied by
# display_scale() so it grows with the font instead of clipping it (WIN-009).
DPI_BASELINE_SCALING = 96 / 72


def display_scale(widget):
    """Display scaling relative to 100%: 1.0 at 96 dpi, 1.25 at 125%, and so on."""
    try:
        return max(1.0, float(widget.tk.call("tk", "scaling")) / DPI_BASELINE_SCALING)
    except Exception:
        return 1.0


def scaled_pixels(widget, pixels):
    """*pixels* (designed for 100%) at the widget's current display scaling."""
    return int(round(pixels * display_scale(widget)))


def treeview_row_height(root):
    """Row height that still fits the body font at the active Tk scaling.

    A fixed 28px row was correct at 100% and wrong above it: Tk scales the
    body font with display scaling but a literal rowheight does not follow, so
    at 150% the text linespace equals the whole row and Results/Mainboard/
    Sideboard clip their own contents.
    """
    try:
        linespace = int(tkfont.Font(root=root, font=FONT_BODY).metrics("linespace"))
    except Exception:
        return TREEVIEW_MIN_ROW_HEIGHT
    return max(TREEVIEW_MIN_ROW_HEIGHT, linespace + TREEVIEW_ROW_PADDING)


def install_ui_styles(root):
    """Install the complete dark MTG component system on *root*."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    p = PALETTE
    root.configure(bg=p["bg"])

    # Classic Tk widgets and native ttk dropdown listboxes do not inherit ttk.
    root.option_add("*Font", FONT_BODY)
    # ...but ttk labels DO have a -font option, so that default would beat the
    # font their style declares: every heading, dialog title and helper line drew
    # as 10 pt regular (TYP-003).  An empty value hands the font back to the style.
    root.option_add("*TLabel.font", "")
    root.option_add("*Button.font", FONT_BODY)
    root.option_add("*Checkbutton.font", FONT_HELPER)
    root.option_add("*Radiobutton.font", FONT_BODY)
    root.option_add("*Entry.font", FONT_BODY)
    root.option_add("*Listbox.font", FONT_BODY)
    root.option_add("*Menu.font", FONT_BODY)
    root.option_add("*Menu.background", p["surface2"])
    root.option_add("*Menu.foreground", p["text"])
    root.option_add("*Menu.activeBackground", p["accent"])
    root.option_add("*Menu.activeForeground", p["on_accent"])
    root.option_add("*Menu.relief", "flat")
    root.option_add("*Menu.borderWidth", 0)
    root.option_add("*Entry.highlightThickness", 1)
    root.option_add("*Entry.highlightBackground", p["border"])
    root.option_add("*Entry.highlightColor", p["accent"])
    root.option_add("*Entry.relief", "flat")
    root.option_add("*TCombobox*Listbox.background", p["input"])
    root.option_add("*TCombobox*Listbox.foreground", p["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["text"])
    root.option_add("*TCombobox*Listbox.relief", "flat")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.configure(".", background=p["surface"], foreground=p["text"],
                    font=FONT_BODY)
    style.configure("TFrame", background=p["surface"])
    style.configure("Bg.TFrame", background=p["bg"])
    style.configure("Workspace.TFrame", background=p["surface"],
                    borderwidth=0, relief="flat")
    style.configure("Preview.TFrame", background=p["surface"],
                    borderwidth=0, relief="flat")
    style.configure("MenuBar.TFrame", background=p["surface2"])
    # Search row hover is intentionally surface-only: metrics and control
    # geometry stay unchanged while the logical label/control row reads as one.
    style.configure("SearchHover.TFrame", background=p["search_hover"])

    style.configure("TLabel", background=p["surface"], foreground=p["text"],
                    font=FONT_BODY)
    style.configure("Muted.TLabel", background=p["surface"],
                    foreground=p["muted"], font=FONT_HELPER)
    style.configure("SearchHover.TLabel", background=p["search_hover"],
                    foreground=p["text"], font=FONT_BODY)
    style.configure("SearchHoverMuted.TLabel", background=p["search_hover"],
                    foreground=p["muted"], font=FONT_HELPER)
    style.configure("DialogTitle.TLabel", background=p["surface"],
                    foreground=p["accent"], font=FONT_DIALOG_TITLE)
    style.configure("RaisedDialogTitle.TLabel", background=p["surface2"],
                    foreground=p["accent"], font=FONT_DIALOG_TITLE)
    style.configure("Section.TLabel", background=p["surface"],
                    foreground=p["accent"], font=FONT_HELPER_BOLD)
    # Alert variants of the section heading.  Same weight and metrics as
    # Section.TLabel so swapping styles never reflows the bar; only the
    # foreground changes.  The comparison over-limit flash alternates between
    # them, so feature code needs no local font tuple or colour.
    style.configure("SectionAlert.TLabel", background=p["surface"],
                    foreground=p["bad_bright"], font=FONT_HELPER_BOLD)
    style.configure("SectionAlertDim.TLabel", background=p["surface"],
                    foreground=p["bad"], font=FONT_HELPER_BOLD)
    # "Working" flash variants of the section heading.  Same metrics as the idle
    # style so a style swap never reflows the bar; the inline PulseStatus
    # alternates bright/dim gold to read as busy (never the red reserved for
    # errors).
    style.configure("SectionWorking.TLabel", background=p["surface"],
                    foreground=p["working"], font=FONT_HELPER_BOLD)
    style.configure("SectionWorkingDim.TLabel", background=p["surface"],
                    foreground=p["working_dim"], font=FONT_HELPER_BOLD)
    # The centered activity cue between the Search and Add buttons: the same gold
    # pair at FONT_ACTIVITY so a busy app is obvious at a glance.  No label
    # padding/border, so the large font is no taller than the buttons beside it
    # (a taller label would grow the whole action row).
    style.configure("ActivityWorking.TLabel", background=p["surface"],
                    foreground=p["working"], font=FONT_ACTIVITY,
                    padding=0, borderwidth=0)
    style.configure("ActivityWorkingDim.TLabel", background=p["surface"],
                    foreground=p["working_dim"], font=FONT_ACTIVITY,
                    padding=0, borderwidth=0)
    style.configure("PreviewCard.TLabel", background=p["surface"],
                    foreground=p["muted"], font=FONT_BODY)
    style.configure("PaneDivider.TSeparator", background=p["border"])

    style.configure("TCheckbutton", background=p["surface"], foreground=p["text"],
                    font=FONT_BODY, indicatorbackground=p["input"],
                    indicatorforeground=p["accent"])
    style.map(
        "TCheckbutton",
        background=[("active", p["surface"])],
        foreground=[("disabled", p["muted"])],
        indicatorbackground=[("selected", p["accent"]),
                             ("active", p["surface3"])],
    )
    style.configure("Color.TCheckbutton", background=p["surface"],
                    foreground=p["text"], font=FONT_BODY)
    style.map(
        "Color.TCheckbutton",
        background=[("active", p["surface"])],
        foreground=[("disabled", p["muted"])],
    )
    style.configure("SearchHover.Color.TCheckbutton", background=p["search_hover"],
                    foreground=p["text"], font=FONT_BODY)
    style.map(
        "SearchHover.Color.TCheckbutton",
        background=[("active", p["search_hover"])],
        foreground=[("disabled", p["muted"])])
    # Mana pip checkbuttons inside the surface2 column-filter popup: match that
    # background so each box does not paint a darker surface-coloured block.
    style.configure("Filter.Color.TCheckbutton", background=p["surface2"],
                    foreground=p["text"], font=FONT_BODY)
    style.map(
        "Filter.Color.TCheckbutton",
        background=[("active", p["surface2"])],
        foreground=[("disabled", p["muted"])])
    style.configure("TRadiobutton", background=p["surface"],
                    foreground=p["text"], font=FONT_BODY,
                    indicatorbackground=p["input"])
    style.map(
        "TRadiobutton",
        background=[("active", p["surface"])],
        indicatorbackground=[("selected", p["accent"]),
                             ("active", p["surface3"])],
    )
    # Single-select rows inside a picker list viewport.  Same themed indicator
    # as TRadiobutton, on the darker `input` list background.  The themed
    # indicator is required: classic Tk radio indicators are painted by Windows
    # itself and ignore this palette, which leaves every row looking selected.
    style.configure("ListChoice.TRadiobutton", background=p["input"],
                    foreground=p["text"], font=FONT_HELPER,
                    indicatorbackground=p["input"])
    style.map(
        "ListChoice.TRadiobutton",
        background=[("active", p["input"])],
        indicatorbackground=[("selected", p["accent"]),
                             ("active", p["surface3"])],
    )
    # Zero-result radio choices remain visible but are explicit unavailable
    # options.  Their text includes a red X marker and the disabled style keeps
    # an unselected zero value from being activated.
    style.configure("Unavailable.ListChoice.TRadiobutton", background=p["input"],
                    foreground=p["bad"], font=FONT_HELPER,
                    indicatorbackground=p["input"])
    style.map(
        "Unavailable.ListChoice.TRadiobutton",
        foreground=[("disabled", p["bad"]), ("active", p["bad"])],
        background=[("active", p["input"])],
        indicatorbackground=[("selected", p["accent"]),
                             ("active", p["surface3"])],
    )

    # Same hollow-then-gold indicator, but painted on the surface the mode row
    # actually sits on. ListChoice carries the input background of a list
    # viewport, which reads as a black box anywhere else.
    for name, ground in (
            ("FormChoice.TRadiobutton", p["surface"]),
            ("DialogChoice.TRadiobutton", p["surface2"]),
            ("SearchHover.FormChoice.TRadiobutton", p["search_hover"]),
    ):
        style.configure(name, background=ground, foreground=p["text"],
                        font=FONT_HELPER, indicatorbackground=p["input"])
        style.map(
            name,
            background=[("active", ground)],
            indicatorbackground=[("selected", p["accent"]),
                                 ("active", p["surface3"])],
        )

    # Role-based buttons. Feature code selects a semantic component role.
    _configure_button(style, "TButton", background=p["surface2"],
                      foreground=p["text"], font=FONT_BODY,
                      padding=PAD_STANDARD, bordercolor=p["border"])
    _map_button(style, "TButton")
    _configure_button(style, "Primary.TButton", background=p["accent"],
                      foreground=p["on_accent"], font=FONT_BODY_BOLD,
                      padding=PAD_PRIMARY, bordercolor=p["accent"], relief="flat")
    _map_button(style, "Primary.TButton", primary=True)
    _configure_button(style, "Compact.TButton", background=p["surface2"],
                      foreground=p["text"], font=FONT_HELPER,
                      padding=PAD_COMPACT, bordercolor=p["border"])
    _map_button(style, "Compact.TButton")
    _configure_button(style, "CompactPrimary.TButton", background=p["accent"],
                      foreground=p["on_accent"], font=FONT_HELPER_BOLD,
                      padding=PAD_COMPACT, bordercolor=p["accent"], relief="flat")
    _map_button(style, "CompactPrimary.TButton", primary=True)
    _configure_button(style, "DeckControl.TButton", background=p["surface2"],
                      foreground=p["text"], font=FONT_BODY,
                      padding=PAD_DECK, bordercolor=p["border"])
    _map_button(style, "DeckControl.TButton")
    _configure_button(style, "Picker.TButton", background=p["surface2"],
                      foreground=p["text"], font=FONT_BODY,
                      padding=PAD_PICKER, bordercolor=p["border"])
    _map_button(style, "Picker.TButton")
    _configure_button(style, "SearchPicker.TButton", background=p["surface2"],
                      foreground=p["text"], font=FONT_BODY,
                      padding=PAD_SEARCH_PICKER, bordercolor=p["border"])
    _map_button(style, "SearchPicker.TButton")
    _configure_button(style, "SearchSection.TButton", background=p["surface3"],
                      foreground=p["accent"], font=FONT_BODY_BOLD,
                      padding=PAD_SEARCH_PICKER, bordercolor=p["accent2"])
    style.map(
        "SearchSection.TButton",
        background=[("disabled", p["surface2"]),
                    ("pressed", p["select"]),
                    ("alternate", p["select"]),
                    ("active", p["select"])],
        foreground=[("disabled", p["muted"]),
                    ("pressed", p["text"]),
                    ("alternate", p["text"]),
                    ("active", p["text"])],
        bordercolor=[("disabled", p["border"]),
                     ("pressed", p["accent"]),
                     ("alternate", p["accent"]),
                     ("active", p["accent"])],
        relief=[("pressed", "sunken")],
    )
    _configure_button(style, "SearchRow.TButton", background=p["surface2"],
                      foreground=p["text"], font=FONT_MICRO,
                      padding=PAD_DENSE, bordercolor=p["border"])
    _map_button(style, "SearchRow.TButton")
    _configure_button(style, "SearchRowAccent.TButton", background=p["accent"],
                      foreground=p["on_accent"], font=FONT_MICRO_BOLD,
                      padding=PAD_DENSE_PRIMARY, bordercolor=p["accent"], relief="flat")
    _map_button(style, "SearchRowAccent.TButton", primary=True)
    style.configure("MenuBar.TMenubutton", background=p["surface2"],
                    foreground=p["text"], font=FONT_BODY,
                    padding=PAD_STANDARD, borderwidth=1, relief="solid",
                    bordercolor=p["border"], lightcolor=p["border"],
                    darkcolor=p["border"], arrowcolor=p["muted"])
    style.map(
        "MenuBar.TMenubutton",
        background=[("pressed", p["select"]), ("alternate", p["select"]),
                    ("active", p["surface3"])],
        foreground=[("active", p["text"])],
        bordercolor=[("disabled", p["border"]),
                     ("pressed", p["accent"]),
                     ("alternate", p["accent"]),
                     ("active", p["border"])],
        arrowcolor=[("active", p["accent"])],
        relief=[("pressed", "sunken")],
    )
    style.configure("Picker.TMenubutton", background=p["surface2"],
                    foreground=p["text"], font=FONT_BODY,
                    padding=PAD_PICKER, borderwidth=1, relief="solid",
                    bordercolor=p["border"], lightcolor=p["border"],
                    darkcolor=p["border"], arrowcolor=p["accent"])
    style.map("Picker.TMenubutton",
              background=[("pressed", p["select"]),
                          ("alternate", p["select"]),
                          ("active", p["surface3"])],
              foreground=[("disabled", p["muted"])],
              bordercolor=[("disabled", p["border"]),
                           ("pressed", p["accent"]),
                           ("alternate", p["accent"]),
                           ("active", p["border"])],
              relief=[("pressed", "sunken")])
    for base_name, role_name in (
        ("TEntry", "Form.TEntry"),
        ("TCombobox", "Form.TCombobox"),
        ("TSpinbox", "Form.TSpinbox"),
    ):
        for name in (base_name, role_name):
            style.configure(
                name,
                fieldbackground=p["input"], background=p["surface2"],
                foreground=p["text"], font=FONT_BODY,
                bordercolor=p["border"], lightcolor=p["border"],
                darkcolor=p["border"], arrowcolor=p["muted"],
                padding=FORM_CONTROL_PADDING,
            )
            style.map(
                name,
                fieldbackground=[("disabled", p["surface3"]),
                                 ("readonly", p["input"]),
                                 ("focus", p["input"])],
                background=[("disabled", p["surface3"])],
                foreground=[("disabled", p["muted"]),
                            ("readonly", p["text"])],
                bordercolor=[("disabled", p["border"]),
                             ("focus", p["accent"])],
                lightcolor=[("disabled", p["border"]),
                            ("focus", p["accent"])],
                darkcolor=[("disabled", p["border"]),
                           ("focus", p["accent"])],
                arrowcolor=[("disabled", p["border"]),
                            ("active", p["accent"]),
                            ("focus", p["accent"])],
            )

    # clam's Treeview.field element has its own one-pixel border even when
    # Treeview borderwidth is zero. Explicitly blend both field edge colors into
    # the surface so Results/Mainboard/Sideboard do not get a light outer box.
    style.configure("Treeview", background=p["surface"],
                    fieldbackground=p["surface"], foreground=p["text"],
                    rowheight=treeview_row_height(root),
                    borderwidth=0, bordercolor=p["surface"],
                    lightcolor=p["surface"], font=FONT_BODY)
    style.configure("Treeview.Heading", background=p["surface2"],
                    foreground=p["muted"], font=FONT_HELPER_BOLD,
                    relief="flat", padding=(6, 7))
    style.map("Treeview.Heading", background=[("active", p["surface3"])],
              foreground=[("active", p["text"])])
    style.map("Treeview", background=[("selected", p["select"])],
              foreground=[("selected", p["text"])])

    style.configure("TPanedwindow", background=p["bg"], sashwidth=6)
    style.configure("TSeparator", background=p["border"])
    scrollbar_arrow = p["on_accent"]
    for name in ("Vertical.TScrollbar", "Dark.Vertical.TScrollbar",
                 "Horizontal.TScrollbar", "Dark.Horizontal.TScrollbar"):
        style.configure(
            name, background=p["accent"], troughcolor=p["surface"],
            bordercolor=p["surface"], darkcolor=p["accent"],
            lightcolor=p["accent"], arrowcolor=scrollbar_arrow,
            arrowsize=13, relief="flat",
        )
        # Disabled means there is nothing to scroll: keep the gutter (so the table
        # width never shifts) but let the thumb melt into the trough instead of
        # a full-length gold bar (UI-016).
        style.map(
            name,
            background=[("disabled", p["surface2"]),
                        ("active", p["accent2"]),
                        ("pressed", p["accent2"])],
            darkcolor=[("disabled", p["surface2"])],
            lightcolor=[("disabled", p["surface2"])],
            arrowcolor=[("disabled", p["border"]),
                        ("active", scrollbar_arrow),
                        ("pressed", scrollbar_arrow)],
        )
    # The Search filter zone's bar while there is nothing to scroll: every part
    # of it paints the surface it sits on, so the gutter stays (the form never
    # reflows when the bar wakes up) but nothing is drawn (LAY-011).
    style.configure(
        "ZoneIdle.Vertical.TScrollbar", background=p["surface"],
        troughcolor=p["surface"], bordercolor=p["surface"],
        darkcolor=p["surface"], lightcolor=p["surface"],
        arrowcolor=p["surface"], arrowsize=13, relief="flat")
    style.map(
        "ZoneIdle.Vertical.TScrollbar",
        background=[("disabled", p["surface"]), ("active", p["surface"])],
        darkcolor=[("disabled", p["surface"])],
        lightcolor=[("disabled", p["surface"])],
        arrowcolor=[("disabled", p["surface"]), ("active", p["surface"])])
    # Results Gallery size control.  Keep the slider in the shared component
    # palette instead of allowing a raw classic-Tk scale to invent its own
    # platform-dependent trough/thumb treatment.
    style.configure(
        "Gallery.Horizontal.TScale", background=p["accent"],
        troughcolor=p["surface3"], bordercolor=p["border"],
        lightcolor=p["accent"], darkcolor=p["accent2"],
        sliderrelief="flat", troughrelief="flat",
    )
    style.map(
        "Gallery.Horizontal.TScale",
        background=[("active", p["accent2"])],
        bordercolor=[("active", p["accent2"])],
    )

    style.configure("Horizontal.TProgressbar", background=p["accent"],
                    troughcolor=p["surface2"], bordercolor=p["surface2"])
    style.configure("Gold.Horizontal.TProgressbar", background=p["accent"],
                    troughcolor=p["input"], bordercolor=p["border"],
                    lightcolor=p["accent"], darkcolor=p["accent2"], thickness=14)
    return style

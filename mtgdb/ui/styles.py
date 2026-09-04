"""Application-wide ttk theme registration.

All ttk dimensions, typography, and state colors are registered here so the
feature UI only selects a semantic component role.
"""

from tkinter import font as tkfont, ttk

from mtgdb.ui.tokens import (
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_DIALOG_TITLE,
    FONT_HELPER,
    FONT_HELPER_BOLD,
    FONT_MICRO,
    FONT_MICRO_BOLD,
    FONT_PANE_TITLE,
    FORM_CONTROL_PADDING,
    PAD_COMPACT,
    PAD_DECK,
    PAD_DENSE,
    PAD_DENSE_PRIMARY,
    PAD_PICKER,
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
    style.configure("Card.TFrame", background=p["surface"],
                    bordercolor=p["border"], borderwidth=1, relief="solid")
    style.configure("Workspace.TFrame", background=p["surface"],
                    borderwidth=0, relief="flat")
    style.configure("Raised.TFrame", background=p["surface2"])
    style.configure("Preview.TFrame", background=p["surface"],
                    borderwidth=0, relief="flat")
    style.configure("MenuBar.TFrame", background=p["surface2"])

    style.configure("TLabel", background=p["surface"], foreground=p["text"],
                    font=FONT_BODY)
    style.configure("Muted.TLabel", background=p["surface"],
                    foreground=p["muted"], font=FONT_HELPER)
    style.configure("Header.TLabel", background=p["surface"],
                    foreground=p["text"], font=FONT_PANE_TITLE)
    style.configure("DialogTitle.TLabel", background=p["surface"],
                    foreground=p["text"], font=FONT_DIALOG_TITLE)
    style.configure("Section.TLabel", background=p["surface"],
                    foreground=p["accent"], font=FONT_HELPER_BOLD)
    # Alert variants of the section heading.  Same weight and metrics as
    # Section.TLabel so swapping styles never reflows the bar; only the
    # foreground changes.  The comparison over-limit flash alternates between
    # them, so feature code needs no local font tuple or colour.
    style.configure("SectionAlert.TLabel", background=p["surface"],
                    foreground=p["deck_bad"], font=FONT_HELPER_BOLD)
    style.configure("SectionAlertDim.TLabel", background=p["surface"],
                    foreground=p["deck_bad_dim"], font=FONT_HELPER_BOLD)
    style.configure("Status.TLabel", background=p["bg"],
                    foreground=p["muted"], font=FONT_HELPER)
    style.configure("PreviewHeader.TLabel", background=p["surface"],
                    foreground=p["text"], font=FONT_PANE_TITLE)
    style.configure("PreviewMuted.TLabel", background=p["surface"],
                    foreground=p["muted"], font=FONT_HELPER)
    style.configure("PreviewCard.TLabel", background=p["surface"],
                    foreground=p["muted"], font=FONT_BODY)
    style.configure("Preview.TSeparator", background=p["border"])
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
    style.map("Color.TCheckbutton", background=[("active", p["surface"])])
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
                fieldbackground=[("readonly", p["input"]),
                                 ("focus", p["input"])],
                foreground=[("readonly", p["text"])],
                bordercolor=[("focus", p["accent"])],
                lightcolor=[("focus", p["accent"])],
                darkcolor=[("focus", p["accent"])],
                arrowcolor=[("active", p["accent"]),
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
        style.map(
            name,
            background=[("active", p["accent2"]),
                        ("pressed", p["accent2"])],
            arrowcolor=[("active", scrollbar_arrow),
                        ("pressed", scrollbar_arrow)],
        )
    style.configure("Horizontal.TProgressbar", background=p["accent"],
                    troughcolor=p["surface2"], bordercolor=p["surface2"])
    style.configure("Gold.Horizontal.TProgressbar", background=p["accent"],
                    troughcolor=p["input"], bordercolor=p["border"],
                    lightcolor=p["accent"], darkcolor=p["accent2"], thickness=14)
    return style

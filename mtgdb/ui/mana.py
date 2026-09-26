"""Mana-symbol asset loading and Tk image composition."""

import logging
import os
import re

from mtgdb.ui.assets import _asset_path
from mtgdb.ui.tokens import (
    FILTER_PIP_SIZE, MANA_BORDER, MANA_COLORLESS_GLYPH_FILL,
    MANA_COLORLESS_GLYPH_OUTLINE, MANA_FALLBACK_FILL, MANA_FALLBACK_OUTLINE,
    MANA_FALLBACK_TEXT, MANA_FILL,
)

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

log = logging.getLogger("mtg")

COST_CACHE_LIMIT = 512
SYMBOL_CACHE_LIMIT = 128

COLOR_ICON_FILES = {"W": "mana/sym_W.png", "U": "mana/sym_U.png",
                    "B": "mana/sym_B.png", "R": "mana/sym_R.png",
                    "G": "mana/sym_G.png", "C": "mana/sym_C.png"}


class ManaSymbolsMixin:
    """Own mana pip and mana-cost image rendering used by the application UI."""

    def _make_pip(self, key, size=16):
        """Draw a small mana pip; C uses the recognizable colorless diamond."""
        scale = 4
        s = size * scale
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        b = scale
        d.ellipse([b, b, s - b, s - b], fill=MANA_FILL[key],
                  outline=MANA_BORDER[key], width=scale)

        if key == "C":
            # Approximate the standard {C} symbol without adding another
            # external asset: dark diamond centered in the gray mana circle.
            cx = cy = s / 2
            r = s * 0.25
            d.polygon(
                [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
                fill=MANA_COLORLESS_GLYPH_FILL,
                outline=MANA_COLORLESS_GLYPH_OUTLINE,
            )

        return ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))


    def _make_pips(self):
        self.pips = {}
        self.pips_disabled = {}
        if not HAVE_PIL:
            return
        for k, fname in COLOR_ICON_FILES.items():
            try:
                img = Image.open(_asset_path(fname)).convert("RGBA")
                img = img.resize((FILTER_PIP_SIZE, FILTER_PIP_SIZE), Image.LANCZOS)
                self.pips[k] = ImageTk.PhotoImage(img)
            except Exception:
                log.warning("Could not load mana icon %s; using drawn pip", fname)
                # The drawn fallback is already a Tk image, so retain it as the
                # normal state and leave disabled-state rendering to ttk.
                self.pips[k] = self._make_pip(k, FILTER_PIP_SIZE)
                continue

            # Search facet availability is communicated by disabling individual
            # mana choices.  A normal PhotoImage remains fully saturated under
            # ttk's disabled state on Windows, so keep a deliberately muted
            # grayscale partner for the state-specific image specification.
            alpha = img.getchannel("A")
            disabled = ImageOps.grayscale(img.convert("RGB")).convert("RGBA")
            disabled.putalpha(alpha.point(lambda value: int(value * 0.52)))
            self.pips_disabled[k] = ImageTk.PhotoImage(disabled)

    def _filter_pip_image(self, key):
        """Return the normal/disabled image specification for a filter pip."""
        normal = self.pips.get(key)
        if normal is None:
            return None
        disabled = getattr(self, "pips_disabled", {}).get(key)
        if disabled is None:
            return normal
        return (normal, "disabled", disabled)


    def _load_symbol_keys(self):
        self._sym_keys = set()
        try:
            for f in os.listdir(_asset_path("mana")):
                if f.startswith("sym_") and f.endswith(".png"):
                    self._sym_keys.add(f[4:-4])
        except OSError:
            pass


    def _resolve_symbol_key(self, token):
        """Map a cost token (contents of {...}) to an extracted symbol key."""
        t = token.upper().strip()
        if t in self._sym_keys and (t.isdigit() or t in
                                    ("W", "U", "B", "R", "G", "C", "S",
                                     "X", "Y", "Z", "T")):
            return t
        if "/" in t:
            parts = [p for p in t.split("/") if p]
            if "P" in parts:                      # Phyrexian, e.g. W/P
                col = [p for p in parts if p != "P"]
                if col and (col[0] + "P") in self._sym_keys:
                    return col[0] + "P"
            if "2" in parts:                      # twobrid, e.g. 2/W
                col = [p for p in parts if p != "2"]
                if col and ("2" + col[0]) in self._sym_keys:
                    return "2" + col[0]
            if len(parts) == 2 and all(p in "WUBRG" for p in parts):
                for key in (parts[0] + parts[1], parts[1] + parts[0]):
                    if key in self._sym_keys:
                        return key
        return None


    def _fallback_symbol(self, token, height):
        """A drawn grey pip with the token text, for symbols not in the sheet."""
        scale = 4
        s = height * scale
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([scale, scale, s - scale, s - scale],
                  fill=MANA_FALLBACK_FILL, outline=MANA_FALLBACK_OUTLINE,
                  width=max(1, scale // 2))
        txt = token.upper().replace("/", "")[:3]
        font = None
        for fname in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "segoeuib.ttf"):
            try:
                font = ImageFont.truetype(fname, int(s * 0.52))
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        bb = d.textbbox((0, 0), txt, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        d.text(((s - tw) / 2 - bb[0], (s - th) / 2 - bb[1]), txt,
               fill=MANA_FALLBACK_TEXT, font=font)
        return img.resize((height, height), Image.LANCZOS)


    def _symbol_pil(self, token, height):
        ck = (token, height)
        if ck in self._sym_scaled:
            return self._sym_scaled[ck]
        key = self._resolve_symbol_key(token)
        img = None
        if key:
            try:
                img = Image.open(_asset_path(f"mana/sym_{key}.png")).convert("RGBA")
                img = img.resize((height, height), Image.LANCZOS)
            except Exception:
                img = None
        if img is None:
            img = self._fallback_symbol(token, height)
        self._sym_scaled[ck] = img
        try:
            self._sym_scaled.move_to_end(ck)
            while len(self._sym_scaled) > SYMBOL_CACHE_LIMIT:
                self._sym_scaled.popitem(last=False)
        except AttributeError:
            pass
        return img


    def _cost_image(self, cost_str, height=18):
        """Composite a mana cost like '{2}{W}{U}' into one small strip image.
        For split / Room / DFC costs stored as 'front // back', only the front
        face's cost is shown (matching how card lists display cost)."""
        if not HAVE_PIL or not cost_str:
            return None
        ck = (cost_str, height)
        if ck in self._cost_cache:
            photo = self._cost_cache[ck]
            try:
                self._cost_cache.move_to_end(ck)
            except AttributeError:
                pass
            return photo
        front = cost_str.split("//")[0]
        tokens = re.findall(r"\{([^}]+)\}", front)
        imgs = [self._symbol_pil(t, height) for t in tokens]
        imgs = [i for i in imgs if i is not None]
        if not imgs:
            return None
        gap = 1
        width = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
        strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        x = 0
        for i in imgs:
            strip.paste(i, (x, 0), i)
            x += i.width + gap
        photo = ImageTk.PhotoImage(strip)
        self._cost_cache[ck] = photo
        try:
            self._cost_cache.move_to_end(ck)
            while len(self._cost_cache) > COST_CACHE_LIMIT:
                self._cost_cache.popitem(last=False)
        except AttributeError:
            pass
        return photo


# MTG Deck Builder

A fast, portable desktop deck builder for **Magic: The Gathering**. Search the
full card pool with rich live filters, build and analyze decks, compare printings
side by side at full art, and print proxy sheets — all offline after the first
data sync.

Windows desktop app (Python + Tkinter). No account, no telemetry; your decks and
settings stay on your machine.

## Download & run (Windows)

1. Download the latest `MTG_Deck_Builder-*-windows.zip` from the
   [**Releases page**](https://github.com/RudeLeft/MTG_Deck_Builder/releases/latest).
2. Unzip it and keep the `MTGDeckBuilder` folder together.
3. Run `MTGDeckBuilder.exe`.

**First-run Windows warning:** the app isn't code-signed yet, so Windows
SmartScreen may show *"Windows protected your PC."* This is expected for a new
independent app. Click **More info → Run anyway** to start it. (SmartScreen
stops warning once the app has been run a few times, or once signing is added.)

No Python required. The app is fully portable — it keeps its card database and
your saved decks in its own folder and writes nothing to AppData or the registry,
so you can copy the folder between PCs or run it from a USB stick.

**Updating:** when a newer release exists, a banner appears at the top of the
window. Click **Update** and the app downloads and verifies the new version, then
**Restart now** swaps it in and relaunches — your `data\` folder (decks, database,
images, settings) is preserved, so there's no re-download of card data. (The first
launch of a freshly downloaded build may show the SmartScreen prompt again until
the app is signed.)

On first launch it downloads current card data from [Scryfall](https://scryfall.com)
(a few hundred MB); after that it runs offline and refreshes on its own schedule.

## Features

- **Search** the full card pool with live, contextual filters — colors, mana
  value, power/toughness, types, subtypes, keywords, formats, rarity, and more —
  each option showing how many cards still match your other filters.
- **Deck building** with mainboard and sideboard, quantities, and basic
  format-legality checks.
- **Comparison view** — line up multiple printings side by side at full art to
  pick your favorite.
- **Proxy printing** to PDF — pulls Scryfall's high-resolution card PNGs so
  printed cards stay crisp, laid out at the correct size with cut guides.
- **Portable & private** — everything lives in one folder; nothing leaves your
  machine except card-data downloads from Scryfall.

## Screenshots

Search and browse the full card pool, with a live gallery preview and a deck
panel showing composition, mana curve, and draw odds:

![The main window: search filters, results table, card preview, and deck panel](assets/screenshots/main-window.webp)

The full-art results gallery, with an adjustable card size:

![Results gallery of full-art cards](assets/screenshots/results-gallery.webp)

Building a deck, with live mana curve, opening-hand draw odds, a sample hand, and
format-legality checks:

![Deck building with mana curve, draw odds, and legality checks](assets/screenshots/deck-building.webp)

Proxy sheets exported to PDF — the app pulls Scryfall's high-resolution card
PNGs so the printed cards stay crisp, laid out at the correct size with cut
guides for trimming:

![A proxy sheet PDF: a grid of full-art cards with cut guides](assets/screenshots/proxy-print.webp)

## Build from source

Requires **Python 3.11+**.

```bash
pip install .
python -m mtgdb
```

To build the standalone Windows executable yourself, run `build_windows.bat`. It
creates an isolated build environment, runs the project's checks, and produces
the portable app in `dist/MTGDeckBuilder/`.

The test suite is a set of standalone scripts under `tests/`. For fast local
iteration, run them all in parallel:

```bash
python tests/run_tests.py
```

It prints a PASS/FAIL summary and the full output of any failure. (The build
script and CI run the same tests one at a time for deterministic, early-exit
per-file attribution.)

### Code signing (optional, removes the SmartScreen warning)

Signed builds skip the SmartScreen prompt above and build download reputation
faster. To sign a build you need a code-signing certificate (an OV certificate
is inexpensive; an EV certificate clears SmartScreen immediately but costs more).
With a `.pfx` in hand, sign the built exe with the Windows SDK `signtool`:

```
signtool sign /f cert.pfx /p <password> /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 dist\MTGDeckBuilder\MTGDeckBuilder.exe
```

In CI, store the certificate and password as encrypted repository secrets and
add a signing step after the "Build the portable app" step in
`.github/workflows/build-windows.yml`, before the release is packaged.

## Card data & legal

**This is unofficial, fan-made software, provided as-is. Nothing here is legal
advice, and you are responsible for how you use it.**

Card data and imagery come from [Scryfall](https://scryfall.com). Magic: The
Gathering, card names, and card art are the intellectual property of Wizards of
the Coast; this project claims no ownership of them.

**About printing proxies:** this tool can print card images for **personal,
non-commercial use** — playtesting and casual games, ideally with cards you own.
Please don't sell printed cards, misrepresent them as genuine, or use them in
sanctioned tournaments or any venue that prohibits proxies. Wizards' Fan Content
Policy does **not** authorize creating proxy cards, so personal proxies are
strictly between you and the law in your area — use this feature responsibly and
at your own risk.

MTG Deck Builder is unofficial Fan Content permitted under the
[Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy). Not
approved/endorsed by Wizards. Portions of the materials used are property of
Wizards of the Coast. ©Wizards of the Coast LLC.

## License

Released under the [MIT License](LICENSE) — do whatever you like with the code,
just keep the copyright notice. The license covers this project's own source
only, not Magic: The Gathering card data or imagery (see above).

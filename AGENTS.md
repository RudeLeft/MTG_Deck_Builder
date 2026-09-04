# MTG Deck Builder — Agent Operating Contract

This file is the complete and authoritative operating contract for every AI
agent and developer who inspects, changes, tests, builds, or packages this
project. It is the single source of truth: where code lives, what each module
owns, what may import what, how to name and place new code, and how every
change is verified. It contains current rules only.

Read it top to bottom before your first change. The five navigation sections —
**Repository map**, **Feature map**, **Module ownership**, **Module map**, and
**Import allow/deny matrix** — provide the routing index for locating an owner
before an edit. When a request names a feature ("change search", "fix the mana curve"),
start with that Feature-map row, use Module ownership to choose the file that
owns the behavior, and use the import matrix before crossing a boundary.

## Authority and interpretation

Normative terms have fixed meanings:

| Term | Meaning |
| --- | --- |
| **MUST** | Mandatory. Failure blocks completion and release. |
| **MUST NOT** | Prohibited. Violation blocks completion and release. |
| **SHOULD** | Required default. Departure requires the user's explicit approval. |
| **SHOULD NOT** | Prohibited default. Departure requires the user's explicit approval. |
| **MAY** | Optional within all other rules. |

Every enforceable rule has a unique ID and one verification class:

| Class | Evidence required |
| --- | --- |
| **AUTO** | A deterministic test or packaging check must pass. |
| **WINDOWS** | A deterministic check must pass on Windows. |
| **REVIEW** | The agent must inspect the affected source or diff and report the result accurately. |
| **MANUAL** | A person must exercise and visually inspect the affected interface. |
| **USER** | The user must explicitly approve the exception or decision. |

- **GOV-001 — MUST:** Read this file completely before inspecting, planning,
  editing, testing, building, or packaging. _Verification:_ **REVIEW**.
- **GOV-002 — MUST:** Treat every MUST/MUST NOT as a release blocker when it
  governs the changed files or release type. _Verification:_ **REVIEW**.
- **GOV-003 — MUST:** When the user's explicit instruction changes a project
  decision, update the affected rule and any affected navigation table in this
  file in the same change, so the repository keeps one current source of truth.
  _Verification:_ **REVIEW**.
- **GOV-004 — MUST:** Stop before implementation when a request conflicts with
  an unchanged rule, name the exact rule ID, and obtain explicit user approval
  for the exception or rule change. _Verification:_ **USER**.
- **GOV-005 — MUST NOT:** Claim a test, platform check, visual review, or user
  approval occurred unless it actually occurred. _Verification:_ **REVIEW**.
- **GOV-006 — MUST:** Demonstrate AUTO/WINDOWS compliance by successful command
  exit status, not by assertion. _Verification:_ **AUTO**.
- **GOV-007 — MUST:** Refuse delivery and packaging while any governing
  mandatory rule has a known violation or failed verification.
  _Verification:_ **REVIEW**.

## Repository map

The project is one importable package, `mtgdb`, organized **layer-first**: all
presentation lives under `mtgdb/ui/`, and each non-UI concern is its own
domain package. This makes the project's central invariant physical — see
LAYER-001.

```
mtgdb/
  __main__.py            # `python -m mtgdb` entry; calls main.main()
  main.py                # startup, logging, single-instance, portable data dir
  core/                  # shared, domain-agnostic, Tk-free
    cache_names.py       #   collision-safe cache path/name construction
    scryfall_json.py     #   parse Scryfall JSON list columns stored as text
    net.py               #   Scryfall HTTP transport (headers, throttling, retries, length-checked downloads)
    background_jobs.py   #   JobCancelled, check_cancel, spawn_daemon, GenerationalWorker
  search/                # interactive search domain (Tk-free)
    models.py            #   SearchCriteria, worker events, result contracts
    repository.py        #   search DB gateway, narrow projection, suggestions, catalogs
    controller.py        #   query worker lifecycle, generations/invalidation, terminal-event queue, cache
    results.py           #   compact SearchResultStore, pure table semantics, async view/vocabulary preparation
    catalogs.py          #   bounded trusted-taxonomy snapshots + latest-wins async discovery
  deck/                  # deck domain (Tk-free, no sqlite)
    model.py             #   Deck: exact-printing entries, quantities, board mutations
    io.py                #   portable TXT serialization, atomic save + section parsing
    file_jobs.py         #   Tk-free background submission for deck import/save/export file work
    analysis.py          #   stats, type classification, mana, probability, curves, hands
    legality.py          #   verified basic construction profiles, copy limits, legality normalization/status
    sessions.py          #   DeckSession / DeckSessionManager: open-deck state
  database/              # Scryfall database internals (Tk-free)
    authorities.py       #   declarative understood upstream taxonomy authorities + compatibility metadata
    constants.py         #   colors, content/layout/playable-legality vocabulary
    db.py                #   CardDB façade (lookup + search + taxonomy mixins)
    schema.py            #   schema v10, columns, indexes, connections, migration
    semantics.py         #   rules normalization, type-line repair, SQLite type functions
    bulk_import.py       #   row projection, strict streaming JSON/JSONL/gzip, verified bulk replace
    queries.py           #   exact-printing lookup, resolver ranking, name suggestions
    search_queries.py    #   SearchQueryBuilder + stable CardDB.search implementation
    taxonomy.py          #   trusted observed sets/formats/types/properties/subtypes/mechanics
    sync.py              #   Scryfall + Wizards taxonomy sync; DatabaseSyncService/Controller (Tk-free)
  comparison/            # comparison domain (Tk-free)
    models.py            #   ComparisonCollection, enforced limits, normalization
  images/                # card-image domain (Tk-free)
    service.py           #   CardImageService: bounded workers, cache, dedup, stale-partial cleanup
  printing/              # print domain (Tk-free)
    renderer.py          #   card/page geometry, placement, cut borders, PDF render
    service.py           #   PrintJob, PrintTemplateService, PrintController, create_print_template
  workspace/             # workspace persistence (Tk-free, no sqlite)
    repository.py        #   versioned schema, async load/hydration, atomic JSON, recovery snapshots, latest-wins writer
  preferences/           # UI preferences persistence (Tk-free)
    repository.py        #   atomic preference read/write preserving unrelated keys
  ui/                    # ALL presentation. Only this package may import tkinter.
    app.py               #   DeckBuilderApp: composition, shared services, shell state/callbacks
    tokens.py            #   palette, typography, spacing, metrics, icon sizes
    styles.py            #   ttk theme registration and state appearance
    assets.py            #   bundled-asset path resolution + PIL availability
    components.py        #   behavior-neutral controls, classic Tk wrappers, tooltips
    autocomplete.py      #   AutocompleteEntry + hidden-first suggestion popup behavior
    tables.py            #   shared table schema, formatting, sort, columns, reorder
    card_detail.py       #   main card preview, Legality/Rotate/Zoom actions, legality/text fallbacks
    search.py            #   trusted main/advanced filters, criteria/workspace capture
    search_printings.py  #   Search adapter/summary for the shared Printings picker
    search_checklist.py  #   hidden-first reusable search checklist dialog
    set_filters.py       #   shared Printings controller/popup + observed set controls
    table_filters.py     #   Results/Mainboard/Sideboard smart filter behavior
    results.py           #   128-row virtual Search viewport, logical selection, preview/full-row hydration
    comparison.py        #   fixed scrollbar-free large-card grid for comparison + sample-hand viewing
    comparison_controls.py #  global comparison bar, mixed-selection actions, source-board provenance, card-grid coordination
    deck.py              #   deck-pane layout, deck tabs, multi-select board mutation callbacks
    deck_files.py        #   deck open/save/import composition + JSON export workflow
    deck_stats.py        #   stats pane: curve, mana, odds, sample hands + large-hand viewer, legality
    workspace.py         #   session/geometry coordination, restored selection, autosave scheduling
    database_sync.py     #   refresh scheduling, progress dialog, Tk polling
    printing.py          #   print destination, progress dialog, Tk polling
    mana.py              #   mana pip/symbol assets and Tk image composition
    window.py            #   WindowServicesMixin: geometry, dark titlebars, scroll, DPI, icons
assets/                  # application icon and bundled mana symbols
tests/                   # cross-platform functional, architecture, contract gates
windows_tests/           # Windows single-instance, packaged smoke, simulated Tk-scaling geometry
MTGDeckBuilder.spec      # portable PyInstaller ONEDIR definition and asset list
pyproject.toml           # Python version, runtime deps, build deps, package discovery
build_windows.bat        # local Windows verification + build orchestration
.github/workflows/build-windows.yml  # cloud Windows verification/build/artifact
.github/workflows/taxonomy-audit.yml  # scheduled/manual full upstream taxonomy audit
package_release.py       # mandatory source-release gates, validation, ZIP creation
.gitignore               # excludes build/dist/build-venv, portable data, caches, archives
.gitattributes           # pins LF line endings so a Windows checkout cannot rewrite every file
```

## Feature map

Each row lists the owning files and direct collaborators a change to that feature
should inspect first. Presentation files are under `mtgdb/ui/`; the rest are
Tk-free logic. The table is a routing index, not a transitive dependency dump:
Module ownership decides where behavior belongs and the import matrix decides
which dependency directions are legal.

| Feature | Presentation | Logic / data |
| --- | --- | --- |
| Search | `mtgdb/ui/search.py`, `mtgdb/ui/search_printings.py`, `mtgdb/ui/search_checklist.py`, `mtgdb/ui/set_filters.py`, `mtgdb/ui/table_filters.py`, `mtgdb/ui/results.py`, `mtgdb/ui/tables.py` | `mtgdb/search/models.py`, `mtgdb/search/repository.py`, `mtgdb/search/controller.py`, `mtgdb/search/results.py`, `mtgdb/search/catalogs.py`, `mtgdb/database/db.py`, `mtgdb/database/constants.py`, `mtgdb/database/search_queries.py`, `mtgdb/database/taxonomy.py` |
| Card preview & images | `mtgdb/ui/card_detail.py`, `mtgdb/ui/styles.py`, `mtgdb/ui/tokens.py` | `mtgdb/images/service.py`, `mtgdb/core/cache_names.py`, `mtgdb/core/net.py`, `mtgdb/database/constants.py`, `mtgdb/deck/legality.py` |
| Deck editing | `mtgdb/ui/deck.py`, `mtgdb/ui/tables.py`, `mtgdb/ui/search.py`, `mtgdb/ui/search_checklist.py`, `mtgdb/ui/comparison_controls.py` | `mtgdb/deck/model.py`, `mtgdb/deck/sessions.py` |
| Deck file open/save/import/export | `mtgdb/ui/deck_files.py`, `mtgdb/ui/set_filters.py` | `mtgdb/deck/io.py`, `mtgdb/deck/file_jobs.py`, `mtgdb/database/db.py`, `mtgdb/database/queries.py`, `mtgdb/database/taxonomy.py` |
| Deck statistics | `mtgdb/ui/deck_stats.py`, `mtgdb/ui/comparison.py`, `mtgdb/ui/comparison_controls.py` | `mtgdb/deck/analysis.py`, `mtgdb/deck/legality.py` |
| Comparison | `mtgdb/ui/comparison.py`, `mtgdb/ui/comparison_controls.py`, `mtgdb/ui/deck.py`, `mtgdb/ui/deck_stats.py` | `mtgdb/comparison/models.py`, `mtgdb/images/service.py` |
| Printing | `mtgdb/ui/printing.py` | `mtgdb/printing/service.py`, `mtgdb/printing/renderer.py`, `mtgdb/core/background_jobs.py`, `mtgdb/core/cache_names.py`, `mtgdb/core/net.py` |
| Database sync | `mtgdb/ui/database_sync.py` | `mtgdb/database/sync.py`, `mtgdb/database/authorities.py`, `mtgdb/database/bulk_import.py`, `mtgdb/database/schema.py`, `mtgdb/core/background_jobs.py`, `mtgdb/core/net.py` |
| Database internals | — | `mtgdb/database/authorities.py`, `mtgdb/database/constants.py`, `mtgdb/database/db.py`, `mtgdb/database/schema.py`, `mtgdb/database/semantics.py`, `mtgdb/database/queries.py`, `mtgdb/database/search_queries.py`, `mtgdb/database/taxonomy.py`, `mtgdb/database/bulk_import.py` |
| Workspace / sessions | `mtgdb/ui/workspace.py`, `mtgdb/ui/search.py` | `mtgdb/workspace/repository.py`, `mtgdb/deck/sessions.py`, `mtgdb/deck/model.py`, `mtgdb/database/db.py`, `mtgdb/database/queries.py` |
| Tables & columns | `mtgdb/ui/tables.py`, `mtgdb/ui/table_filters.py` | `mtgdb/preferences/repository.py`, `mtgdb/search/repository.py` |
| Autocomplete | `mtgdb/ui/autocomplete.py`, `mtgdb/ui/components.py` | — |
| Mana symbols / cost images | `mtgdb/ui/mana.py`, `mtgdb/ui/assets.py` | `assets/mana/*` |
| Window / platform | `mtgdb/ui/window.py`, `mtgdb/ui/assets.py` | `mtgdb/main.py` |
| App shell & shared services | `mtgdb/ui/app.py` | `mtgdb/core/*`, `mtgdb/search/controller.py`, `mtgdb/search/repository.py`, `mtgdb/database/db.py`, `mtgdb/database/sync.py`, `mtgdb/images/service.py`, `mtgdb/printing/service.py`, `mtgdb/deck/model.py`, `mtgdb/deck/sessions.py`, `mtgdb/comparison/models.py` |
| Startup / packaging | — | `mtgdb/__main__.py`, `mtgdb/main.py`, `MTGDeckBuilder.spec`, `pyproject.toml`, `build_windows.bat`, `.github/workflows/build-windows.yml`, `.github/workflows/taxonomy-audit.yml`, `package_release.py` |

Data-shape routing: when a Search or configurable Results/Table request needs a card field that is not already stored, inspect `mtgdb/database/schema.py` and `mtgdb/database/bulk_import.py` first, then update the Search/table owners. UI or Search modules must not synthesize a missing stored Scryfall field.

Taxonomy routing: selectable Search vocabulary comes from `mtgdb/database/taxonomy.py` and the current local Scryfall snapshot. Scryfall catalogs authorize Card Types, Subtypes, and Mechanics; Supertypes are authorized by a strictly parsed, provenance-recorded current Wizards Comprehensive Rules TXT discovered from the official Rules page; observed Scryfall row fields authorize Sets, Set Types, Formats, and Rarities. Every picker taxonomy value must be both upstream-authorized and present in the current local Content/Paper scope. UI/workspace code may select existing vocabulary but may not create it.

## Module ownership

Each module has one exclusive responsibility. A fact about behavior X is edited
only in the module that owns X.

| Module | Exclusive responsibility |
| --- | --- |
| `mtgdb/__main__.py` | Module entry point that delegates `python -m mtgdb` to `main.main()` |
| `mtgdb/main.py` | Startup, logging, single-instance handling, portable runtime-directory init |
| `mtgdb/ui/app.py` | Main-window composition, cross-feature state, shared services, mixin assembly and compatibility delegation |
| `mtgdb/core/cache_names.py` | Stable cache naming and path construction |
| `mtgdb/core/scryfall_json.py` | Shared parsing of Scryfall JSON list columns (notably `card_faces`) that bulk import stores as text |
| `mtgdb/core/net.py` | Scryfall HTTP behavior, headers, throttling, transient retries, declared-length verification, partial cleanup |
| `mtgdb/core/background_jobs.py` | Shared cancellation exception, cooperative cancel check, daemon-thread factory, and generation-tagged single-worker controller for printing and syncing |
| `mtgdb/search/models.py` | Immutable search criteria, signatures, worker events, result contracts |
| `mtgdb/search/repository.py` | Interactive-search DB gateway, narrow projection, name suggestions, filter catalogs |
| `mtgdb/search/controller.py` | Tk-free search worker lifecycle, generation invalidation, stale-event rejection, terminal-event queue, bounded cache |
| `mtgdb/search/results.py` | Compact immutable Search result rows/store, exact-printing hydration cache, pure table value/filter/sort semantics, complete logical view indexes, generation-protected Results and vocabulary preparation |
| `mtgdb/search/catalogs.py` | Bounded Content/Paper/Set-Type trusted-taxonomy snapshot caches and latest-wins background catalog discovery |
| `mtgdb/deck/model.py` | Exact-printing deck state, quantities, board mutations, entry access, compat delegation |
| `mtgdb/deck/io.py` | Portable TXT serialization, atomic user-selected TXT save, section parsing, printing tags, resolver import |
| `mtgdb/deck/file_jobs.py` | Tk-free daemon submission wrapper for deck import/save/export file work |
| `mtgdb/deck/analysis.py` | Statistics, type classification, mana requirements/sources, probability, curves, sample hands |
| `mtgdb/deck/legality.py` | Verified basic format construction profiles, Oracle-text/basic-land copy-limit handling, shared Scryfall legality normalization, and banned/restricted/not-legal status checks |
| `mtgdb/deck/sessions.py` | Typed open-deck sessions, active-index lifecycle, dirty state, default-session enforcement |
| `mtgdb/database/authorities.py` | Declarative registry of upstream Scryfall catalogs the application understands, their semantic roles, subtype applicability, and compatibility metadata keys; never value whitelists |
| `mtgdb/database/constants.py` | Shared database/search semantics: colors, trusted content classes, known/playable legality statuses, known layout classes, and preferred rarity display order |
| `mtgdb/database/db.py` | Stable `CardDB` façade, metadata/catalog composition, lifecycle delegation, public API |
| `mtgdb/database/schema.py` | Schema version, columns, indexes, connection policy, query-function registration, migration |
| `mtgdb/database/semantics.py` | Rules normalization, type-line repair, phrase-safe type/subtype matching, stable Cards/Tokens/Emblems/Art Series classification plus internal unknown-layout classification, SQLite semantic functions |
| `mtgdb/database/bulk_import.py` | Row projection, strict streaming JSON/JSONL/gzip parsing, transactional bulk replacement, distinct-row threshold protection, index rebuild |
| `mtgdb/database/queries.py` | Exact-printing lookup, import-resolver ranking, and name suggestions |
| `mtgdb/database/search_queries.py` | `SearchQueryBuilder`, canonical parameterized search SQL, projection validation, stable `CardDB.search()` implementation |
| `mtgdb/database/taxonomy.py` | Trusted observed Search vocabulary plus internal upstream compatibility fingerprints/reports: sets, set types, formats, rarities, catalog-backed Card Types/Subtypes/Mechanics, and Wizards-rules-backed Supertypes, scoped by Content and Paper-only |
| `mtgdb/database/sync.py` | Tk-free Scryfall bulk/catalog retrieval plus official Wizards Rules TXT discovery/parsing/provenance, 48-hour/taxonomy-completeness due policy, internal post-refresh compatibility diagnostics, download cleanup, progress, cancellation, single-worker lifecycle, typed events |
| `mtgdb/comparison/models.py` | Tk-free comparison collection: ordered exact-printing membership, add/remove/clear, limits, mutation results |
| `mtgdb/images/service.py` | Tk-free interactive image download, validation, atomic cache, stale-partial cleanup, rotation/resize, bounded priority workers, in-flight dedup, latest-wins channel supersession, independent byte-budgeted decoded/processed LRUs |
| `mtgdb/printing/renderer.py` | Card/page geometry, image placement, cut borders, page counting, atomic PDF rendering |
| `mtgdb/printing/service.py` | Deck snapshots, PNG validation/cache, download orchestration, typed print events, single-worker lifecycle, cancellation |
| `mtgdb/workspace/repository.py` | Versioned workspace schema, exact-printing projection, async load/hydration worker, payload signatures, atomic primary JSON, best-effort bounded recovery, corruption fallback, latest-wins background durability worker |
| `mtgdb/preferences/repository.py` | Tk-free atomic persistence of UI preferences preserving unrelated keys |
| `mtgdb/ui/tokens.py` | Palette, typography, spacing, control metrics, comparison metrics, icon sizes |
| `mtgdb/ui/styles.py` | Global ttk theme registration and ttk state appearance |
| `mtgdb/ui/assets.py` | Bundled-asset path resolution (source and frozen) and shared PIL availability |
| `mtgdb/ui/components.py` | Reusable behavior-neutral controls, classic Tk wrappers, token fields, tooltips |
| `mtgdb/ui/autocomplete.py` | Hidden-first autocomplete-popup lifecycle/navigation plus the current `AutocompleteEntry` control |
| `mtgdb/ui/tables.py` | Shared table schema, headings, column visibility/menus/reordering, monitor-clamped column-popup placement, and delegation to Tk-free value/sort semantics |
| `mtgdb/ui/card_detail.py` | Main card-preview layout/actions, manual rotation, modeless zoom viewer, compact legality popup/text fallback via the deck-legality normalization API, selection generations, latest-preview request channel, Tk-side polling/deferred image completion |
| `mtgdb/ui/search.py` | Trusted Search layout/state: primary Card Type/Supertypes/Colors/numeric/Mechanics filters, Advanced Content/Rules Text/Subtype/Format/Rarity/Printings coordination and alignment, validated criteria, exact-name presets, complete Clear, strict workspace restore, summaries |
| `mtgdb/ui/search_printings.py` | Search adaptation of the shared Printings component: Search button/summary wiring, Search-owned callbacks, Search English/content scope |
| `mtgdb/ui/search_checklist.py` | Hidden-first reusable searchable virtual choice dialog for taxonomy/format/rarity pickers; multi-select uses checkboxes and single-select uses radio controls |
| `mtgdb/ui/set_filters.py` | Shared `PrintingFilter` controller/popup for Paper-only, English, observed Set Type, cascading Exact Set state/lifecycle, bounded two-column Exact Set virtualization, shared catalog enable/disable state, and set-type rendering/shell primitives; no product grouping/default taxonomy |
| `mtgdb/ui/table_filters.py` | Results/Mainboard/Sideboard smart-filter editors/actions/popup lifecycle plus async Results-vocabulary UI adaptation |
| `mtgdb/ui/results.py` | Fixed 128-row Search Results viewport, complete logical scrolling/navigation/selection, focused primary preview, count presentation, async view/vocabulary swap adaptation, exact-printing hydration |
| `mtgdb/ui/comparison.py` | Fixed-size modeless large-card grid shared by comparison and read-only sample-hand viewing: larger printing images, source-board quantity labels, contextual deck/comparison actions, count-independent geometry, shared-service image loading |
| `mtgdb/ui/comparison_controls.py` | Global deck-workspace comparison bar, mixed Results/Mainboard/Sideboard highlighted-card collection and mutations, exact source-session/board provenance, one-copy source removal, Search-origin deck adds, dark notices, reusable card-grid window coordination |
| `mtgdb/ui/deck.py` | Deck-pane layout, deck tabs, session-to-widget coordination, native extended board selection, batch quantity/remove/move callbacks, deck-table reconciliation |
| `mtgdb/ui/deck_files.py` | Compose the shared `PrintingFilter` for Open Deck, own TXT open/save dialogs, JSON projection/export, and user-selected deck-file paths |
| `mtgdb/ui/deck_stats.py` | Stats pane dashboard layout, sectioned scrollable presentation, counted curve/type/color legends, mana presentation, draw odds, current-deck seven-row sample-hand interaction + exact-printing hydration/View hand coordination, basic legality-check presentation |
| `mtgdb/ui/workspace.py` | Workspace/session coordination, detached Tk snapshot capture, retained Mainboard/Sideboard sash capture/restore, restored-result selection, autosave scheduling and writer shutdown/flush |
| `mtgdb/ui/database_sync.py` | Refresh scheduling, progress-dialog presentation, Tk polling, completion reconciliation, error/shutdown adaptation |
| `mtgdb/ui/printing.py` | Print destination selection, progress-dialog presentation, Tk polling, completion/error/shutdown adaptation |
| `mtgdb/ui/mana.py` | Mana pip drawing, bundled symbol loading, fallback symbols, mana-cost Tk image composition and caches |
| `mtgdb/ui/window.py` | `WindowServicesMixin`: popup geometry, per-monitor visible work-area resolution, dark title bars, scrolling, icons, DPI, window behavior |

## Module map

Use this task-oriented map after the Feature map identifies the feature. The
Module ownership table states the exclusive responsibility; this map gives
concrete routing examples, the nearest collaborators to inspect, and a boundary
cue that prevents convenient-but-wrong placement. Examples are representative,
not exhaustive: inspect the current implementation and governing rules before
editing. Every non-empty production module must appear exactly once and must
have at least two routing examples.

| Module | Route here for examples | Usually inspect with | Boundary cue |
| --- | --- | --- | --- |
| `mtgdb/__main__.py` | change `python -m mtgdb` delegation<br>change module-entry exit forwarding | `mtgdb/main.py` | Do not add startup policy or feature logic here. |
| `mtgdb/main.py` | change portable runtime-directory initialization<br>change logging, startup failure, or Windows single-instance startup behavior | `mtgdb/ui/app.py`; `mtgdb/database/db.py` | Do not build Tk widgets or implement feature behavior here. |
| `mtgdb/ui/app.py` | add or replace a shared application service<br>change mixin composition, top-level shell wiring, or cross-feature delegation | `mtgdb/main.py`; owning `mtgdb/ui/*.py` feature module | Keep feature-specific behavior in its owning feature module. |
| `mtgdb/core/cache_names.py` | change collision-safe cache filenames<br>change cache identity/index path construction | `mtgdb/images/service.py`; `mtgdb/printing/service.py` | Do not download, decode, or render images here. |
| `mtgdb/core/scryfall_json.py` | change how stored Scryfall JSON list columns are parsed<br>change well-formed card-face extraction shared by legality, images, and comparison | `mtgdb/deck/legality.py`; `mtgdb/images/service.py`; `mtgdb/comparison/models.py` | Parse only. Card semantics, image selection, and display formatting stay with their owners. |
| `mtgdb/core/net.py` | change shared HTTP headers, Scryfall API throttling, timeout, or retry policy<br>change streaming download length verification or partial-file cleanup | `mtgdb/database/sync.py`; `mtgdb/images/service.py`; `mtgdb/printing/service.py` | Keep domain interpretation, SQL, and Tk out of transport code. |
| `mtgdb/core/background_jobs.py` | change cooperative cancellation behavior<br>change shared daemon/generation worker lifecycle used by sync or printing | `mtgdb/database/sync.py`; `mtgdb/printing/service.py` | Do not add feature-specific progress or payload semantics. |
| `mtgdb/search/models.py` | add/change semantic Search criteria such as Supertypes, Content, or Paper-only<br>change Search worker-event or result-contract dataclasses/signatures | `mtgdb/ui/search.py`; `mtgdb/search/controller.py`; `mtgdb/database/search_queries.py` | No Tk state, taxonomy discovery, or SQL construction belongs here. |
| `mtgdb/search/repository.py` | add a card field required by broad Results rows<br>change Search name-suggestion or filter-catalog gateway behavior | `mtgdb/database/search_queries.py`; `mtgdb/database/taxonomy.py`; `mtgdb/search/models.py` | Keep SQL in database owners and Tk in UI owners. |
| `mtgdb/search/controller.py` | change Search cache or unchanged-search behavior<br>change generation invalidation, stale-event rejection, or worker queue lifecycle | `mtgdb/search/repository.py`; `mtgdb/search/models.py`; `mtgdb/ui/search.py` | Do not read widgets or construct SQL here. |
| `mtgdb/search/results.py` | change compact broad-result storage, exact-printing hydration, or logical result indexes<br>change Tk-free Results filter/sort preparation or complete-set column vocabulary generation | `mtgdb/search/repository.py`; `mtgdb/ui/results.py`; `mtgdb/ui/table_filters.py`; `mtgdb/ui/tables.py` | Never import Tk or make one UI row authoritative for logical result data. |
| `mtgdb/search/catalogs.py` | change trusted Search-taxonomy snapshot cache bounds or scope keys<br>change latest-wins background discovery for Content, Paper, or Set Type scopes | `mtgdb/search/repository.py`; `mtgdb/ui/search.py`; `mtgdb/ui/search_printings.py`; `mtgdb/ui/database_sync.py` | Do not invent vocabulary or touch Tk; repository taxonomy remains authoritative. |
| `mtgdb/deck/model.py` | change card quantity or board-move invariants<br>change exact-printing deck entry identity/access behavior | `mtgdb/deck/io.py`; `mtgdb/deck/sessions.py`; `mtgdb/ui/deck.py` | File formats, UI callbacks, and legality rules have separate owners. |
| `mtgdb/deck/io.py` | change portable TXT syntax or printing tags<br>change atomic TXT save, section parsing, or resolver-driven import | `mtgdb/deck/model.py`; `mtgdb/database/queries.py`; `mtgdb/ui/deck_files.py` | Do not open Tk dialogs or own database ranking here. |
| `mtgdb/deck/file_jobs.py` | change daemon execution/future completion for deck import/save/export work<br>change background exception propagation for deck file jobs | `mtgdb/core/background_jobs.py`; `mtgdb/ui/deck_files.py` | Never import Tk or own deck format semantics; this is execution plumbing only. |
| `mtgdb/deck/analysis.py` | change mana curve/type/source statistics<br>change draw probability or sample-hand calculations | `mtgdb/ui/deck_stats.py`; `mtgdb/deck/model.py` | Keep presentation and format-legality policy out of analysis. |
| `mtgdb/deck/legality.py` | change verified format deck-size/sideboard rules or unsupported-format behavior<br>change copy-limit, legality-payload normalization, banned/restricted/not-legal, or missing-status checks | `mtgdb/ui/deck_stats.py`; `mtgdb/ui/card_detail.py`; `mtgdb/database/constants.py` | This is a basic legality engine, not UI or general deck statistics; its format profiles MUST NOT authorize Search Format vocabulary. |
| `mtgdb/deck/sessions.py` | change active-deck switching/default-session rules<br>change dirty-state or open-session lifecycle | `mtgdb/deck/model.py`; `mtgdb/ui/deck.py`; `mtgdb/ui/workspace.py` | Do not persist workspace files or manipulate widgets here. |
| `mtgdb/database/authorities.py` | change which Scryfall catalogs/concepts this build understands<br>change subtype-family applicability or compatibility metadata keys | `mtgdb/database/sync.py`; `mtgdb/database/taxonomy.py`; `mtgdb/database/schema.py` | Registry entries describe authority semantics only; never add Card Type/Subtype/Mechanic values here and never auto-interpret an unknown endpoint. |
| `mtgdb/database/constants.py` | change color/content/layout vocabulary<br>change known/playable-legality semantics or preferred rarity ordering | `mtgdb/database/search_queries.py`; `mtgdb/database/taxonomy.py`; `mtgdb/database/semantics.py`; `mtgdb/deck/legality.py` | Do not hardcode allowed Card Types, Supertypes, set groupings, Subtypes, Mechanics, Formats, Sets, Set Types, or Rarity membership here. Familiar rarity values MAY define display order only when unknown observed rarities remain accepted. |
| `mtgdb/database/db.py` | add or change a stable public `CardDB` façade method<br>change database mixin composition or connection/lifecycle delegation | `mtgdb/database/schema.py`; `mtgdb/database/queries.py`; `mtgdb/database/search_queries.py`; `mtgdb/database/taxonomy.py` | Do not implement SQL/query algorithms directly in the façade. |
| `mtgdb/database/schema.py` | add a stored card column or index<br>change schema migration, connection policy, or SQLite function registration | `mtgdb/database/bulk_import.py`; `mtgdb/database/semantics.py`; query owners | Do not parse Scryfall payloads or build UI here. |
| `mtgdb/database/semantics.py` | change Oracle/type-line normalization, repair, or exact type/subtype matching<br>change Cards/Tokens/Emblems/Art Series row classification or registered SQLite semantic functions | `mtgdb/database/schema.py`; `mtgdb/database/bulk_import.py`; `mtgdb/database/taxonomy.py`; `mtgdb/database/search_queries.py` | Keep taxonomy vocabulary discovery, schema DDL, transport, and UI outside semantic normalization. |
| `mtgdb/database/bulk_import.py` | map a new Scryfall field into stored rows<br>change strict JSON/JSONL/gzip parsing, replacement threshold, or index rebuild | `mtgdb/database/schema.py`; `mtgdb/database/semantics.py`; `mtgdb/database/sync.py` | Do not own HTTP transport or interactive Search SQL. |
| `mtgdb/database/queries.py` | change exact-printing lookup behavior<br>change imported-card resolver ranking or name suggestions | `mtgdb/database/db.py`; `mtgdb/deck/io.py`; `mtgdb/database/schema.py` | Interactive filter SQL belongs in `database/search_queries.py`. |
| `mtgdb/database/search_queries.py` | add or change a Search criterion's SQL semantics<br>change result projection validation, ordering, or canonical parameterized Search query construction | `mtgdb/search/models.py`; `mtgdb/search/repository.py`; `mtgdb/database/constants.py`; `mtgdb/database/schema.py` | Exact lookup/import ranking belongs in `database/queries.py`; no Tk here. |
| `mtgdb/database/taxonomy.py` | change trusted Card Type/Supertype/Subtype/Mechanic vocabulary or its Content/Paper scope<br>change observed Set/Set Type/Format/Rarity/Exact Set discovery and local-intersection rules | `mtgdb/database/constants.py`; `mtgdb/database/semantics.py`; `mtgdb/database/schema.py`; `mtgdb/search/repository.py` | Picker vocabulary must come from authoritative upstream sources plus local occurrence; Supertypes use only verified Wizards Rules metadata, never Scryfall supertype catalogs or source-code value lists. Do not guess words or accept workspace/UI values as vocabulary. |
| `mtgdb/database/sync.py` | change the 48-hour/taxonomy-completeness refresh-due policy, Scryfall bulk/catalog flow, or Wizards Rules TXT discovery/parsing/provenance<br>change sync cancellation, progress events, download cleanup, or single-worker lifecycle | `mtgdb/core/net.py`; `mtgdb/core/background_jobs.py`; `mtgdb/database/bulk_import.py`; `mtgdb/ui/database_sync.py` | No Tk dialogs and no direct Search-controller manipulation here. |
| `mtgdb/comparison/models.py` | change comparison minimum/maximum or add/remove semantics<br>change exact-printing ordering, normalization, or mutation-result behavior | `mtgdb/ui/comparison_controls.py`; `mtgdb/ui/comparison.py` | Keep window/image presentation out of the comparison domain. |
| `mtgdb/images/service.py` | change interactive image cache/download/dedup, priority, or latest-wins channel supersession behavior<br>change byte-budget/count LRU accounting, oversized retention, image validation, rotation/resize, or worker limits | `mtgdb/core/net.py`; `mtgdb/core/cache_names.py`; `mtgdb/ui/card_detail.py`; `mtgdb/ui/comparison.py` | Final Tk image conversion and preview poll scheduling belong in UI; print images have a separate service. |
| `mtgdb/printing/renderer.py` | change physical card/page dimensions or page grid<br>change PDF image placement, cut borders, page counting, or atomic PDF output | `mtgdb/printing/service.py`; `mtgdb/ui/printing.py` | Do not download/cache images, inspect deck state, or manage workers here. |
| `mtgdb/printing/service.py` | change print-job deck snapshot or image-download preparation<br>change print events, cancellation, PNG validation/cache, or worker lifecycle | `mtgdb/printing/renderer.py`; `mtgdb/core/net.py`; `mtgdb/core/cache_names.py`; `mtgdb/ui/printing.py` | Destination dialogs and progress widgets remain in UI. |
| `mtgdb/workspace/repository.py` | change workspace schema/version, exact-printing projection, background load/hydration lifecycle, or latest-wins writer lifecycle<br>change atomic primary save, recovery retention, corruption fallback, payload signatures, serialization/fsync/replace durability | `mtgdb/deck/sessions.py`; `mtgdb/deck/model.py`; `mtgdb/ui/workspace.py` | Do not know individual Search widgets, database APIs, or Tk geometry controls. |
| `mtgdb/preferences/repository.py` | change persistent table/UI preference keys or migration<br>change atomic preference writes while preserving unrelated keys | `mtgdb/ui/tables.py`; `mtgdb/ui/table_filters.py` | No Tk imports and no feature behavior beyond preference persistence. |
| `mtgdb/ui/tokens.py` | change palette/typography/spacing constants<br>change shared control, comparison, or icon metrics | `mtgdb/ui/styles.py`; `mtgdb/ui/components.py`; `mtgdb/ui/window.py` | Do not create widgets or hard-code feature behavior here. |
| `mtgdb/ui/styles.py` | change ttk theme registration or named widget/surface styles such as borderless `Preview.TFrame`<br>change ttk state-dependent appearance such as hover/disabled/selected styling | `mtgdb/ui/tokens.py`; `mtgdb/ui/components.py`; owning feature UI | Layout behavior and feature callbacks belong in components/features. |
| `mtgdb/ui/assets.py` | change source/frozen bundled-asset resolution<br>change shared PIL availability/fallback handling | `mtgdb/ui/mana.py`; `mtgdb/ui/window.py`; `MTGDeckBuilder.spec` | Do not own image downloading or feature-specific rendering. |
| `mtgdb/ui/components.py` | add a reusable behavior-neutral button/field/control wrapper<br>change tooltip or classic-Tk wrapper behavior shared across screens | `mtgdb/ui/tokens.py`; `mtgdb/ui/styles.py`; consuming UI modules | Feature-specific state/callback semantics stay with the feature owner. |
| `mtgdb/ui/autocomplete.py` | change autocomplete popup keyboard/focus/dismissal mechanics<br>change `AutocompleteEntry` suggestion/commit behavior | `mtgdb/ui/components.py`; consuming Search UI | Keep popup mechanics centralized; do not duplicate them in individual screens. |
| `mtgdb/ui/tables.py` | add/change a shared Results/Mainboard/Sideboard column definition or display delegation<br>change column visibility menu, heading, reset, or drag/reorder behavior | `mtgdb/ui/table_filters.py`; `mtgdb/preferences/repository.py`; `mtgdb/search/repository.py` | A card-data Results column also requires the narrow Search projection to expose the field. |
| `mtgdb/ui/card_detail.py` | change fields/rules shown in the main card preview or the card Legality popup<br>change preview Legality/Rotate/Zoom actions, selection generation, latest-preview channel use, or Tk polling/deferred image-completion behavior | `mtgdb/images/service.py`; `mtgdb/deck/legality.py`; `mtgdb/database/constants.py`; `mtgdb/ui/results.py`; `mtgdb/ui/window.py` | Network/cache workers, legality-payload normalization, decoded-image resize/rotation, queue priority, and disk work stay in their non-UI owners. |
| `mtgdb/ui/search.py` | add/change trusted primary/Advanced Search controls, criteria capture, or scoped taxonomy refresh<br>change strict workspace restore, filter summaries, complete Clear/reset, callbacks, Rules Text editing semantics, or exact-name presets | `mtgdb/search/models.py`; `mtgdb/search/controller.py`; `mtgdb/search/repository.py`; `mtgdb/database/taxonomy.py`; `mtgdb/database/search_queries.py` | Shared printing/set state belongs in `ui/set_filters.py` with Search adaptation in `ui/search_printings.py`; UI MUST consume taxonomy vocabulary, never invent/restore it; missing stored fields route through schema/import first. |
| `mtgdb/ui/search_printings.py` | change how Search opens/summarizes the shared Printings picker<br>change Search-specific content/language callbacks into shared printing state | `mtgdb/ui/set_filters.py`; `mtgdb/ui/search.py`; `mtgdb/database/taxonomy.py` | Paper/Set Type/Exact Set popup behavior belongs in `ui/set_filters.py`; do not duplicate the shared controller here. |
| `mtgdb/ui/search_checklist.py` | change searchable taxonomy/format/rarity choice behavior<br>change hidden-first fixed-row virtualization, filtering, selection, columns, or dismissal | `mtgdb/ui/search.py`; `mtgdb/ui/components.py` | Keep logical values in Python and the physical choice-widget pool bounded; single-select choices use radio controls; table-column filters use `ui/table_filters.py`. |
| `mtgdb/ui/set_filters.py` | change shared Paper-only/English/Set Type/Exact Set picker state, cascading scope, popup lifecycle, fixed-row Exact Set virtualization, shared loading enable/disable behavior, or modal/nonmodal behavior<br>change human-readable rendering or flat controls for observed Scryfall `set_type` | `mtgdb/ui/search_printings.py`; `mtgdb/ui/deck_files.py`; `mtgdb/database/taxonomy.py` | Search and Open Deck MUST compose this shared controller; subclass adapters must not duplicate common catalog-control behavior or define set-type membership/default groups. |
| `mtgdb/ui/table_filters.py` | change numeric/text/categorical filter editor behavior or deck-table filter adaptation<br>change async Results vocabulary popup integration, sorting actions, or lifecycle | `mtgdb/ui/tables.py`; `mtgdb/preferences/repository.py`; owning table screen | Shared column definitions/formatters belong in `ui/tables.py`. |
| `mtgdb/ui/results.py` | change fixed-slot viewport materialization, logical scrolling, or full-count presentation<br>change complete logical multi-selection, focused preview-primary behavior, exact-printing selection preservation, or hydration adaptation | `mtgdb/search/results.py`; `mtgdb/ui/search.py`; `mtgdb/search/repository.py`; `mtgdb/ui/tables.py`; `mtgdb/ui/card_detail.py` | Never use Treeview rows as the Results data store; Search criteria and SQL stay elsewhere. |
| `mtgdb/ui/comparison.py` | change fixed comparison/hand-view geometry, scrollbar-free multirow layout, or per-card rendering<br>change image sizing/loading, source-board quantity labels, comparison/deck action buttons, or read-only static-card presentation | `mtgdb/comparison/models.py`; `mtgdb/images/service.py`; `mtgdb/ui/comparison_controls.py`; `mtgdb/ui/deck_stats.py`; `mtgdb/ui/tokens.py` | Collection/deck mutation policy and window coordination belong in `ui/comparison_controls.py`; statistical hand generation stays in deck analysis. |
| `mtgdb/ui/comparison_controls.py` | change global comparison bar/status, mixed-source highlighted-card collection, Results/deck context comparison actions, dark min/max notices, or exact source-board provenance/one-copy removal<br>change Search-origin deck adds, add/remove/clear/Open Compare, or reusable read-only card-grid window coordination | `mtgdb/comparison/models.py`; `mtgdb/ui/comparison.py`; `mtgdb/ui/deck.py`; `mtgdb/ui/deck_stats.py`; `mtgdb/ui/results.py`; `mtgdb/ui/window.py` | Do not duplicate card-grid rendering, deck mutation internals, or domain limit rules here. |
| `mtgdb/ui/deck.py` | change deck tabs, board controls, independent Mainboard/Sideboard native multi-selection, or session-to-widget coordination<br>change batch quantity/remove/board-move callbacks, multi-selected exact-name Search routing, global comparison-bar placement, comparison-free deck context menus, deck-table reconciliation, or alternate-printing action invocation | `mtgdb/deck/model.py`; `mtgdb/deck/sessions.py`; `mtgdb/ui/tables.py`; `mtgdb/ui/search.py`; `mtgdb/ui/search_checklist.py`; `mtgdb/ui/comparison_controls.py` | Do not manipulate individual Search controls or perform file IO here. |
| `mtgdb/ui/deck_files.py` | change how Open Deck composes the shared Printings picker or passes its scope into TXT import<br>change Open/Save/JSON-export projection, prompts, or user-selected file paths | `mtgdb/deck/io.py`; `mtgdb/deck/file_jobs.py`; `mtgdb/database/queries.py`; `mtgdb/ui/set_filters.py` | MUST NOT implement a second Printings picker; TXT parsing/serialization and resolver ranking stay in non-UI owners, and blocking disk/parser work stays off Tk. |
| `mtgdb/ui/deck_stats.py` | change stats-pane metric-list/curve/mana/type/color presentation or counted legends<br>change draw odds, current-deck sample-hand Draw/View hand interaction, exact-printing hydration, stale-hand cleanup, or basic legality-check presentation | `mtgdb/deck/analysis.py`; `mtgdb/deck/legality.py`; `mtgdb/deck/model.py`; `mtgdb/ui/comparison_controls.py`; `mtgdb/ui/comparison.py` | Hand generation/statistical formulas and legality rules remain Tk-free; large-card grid rendering stays in the comparison view. |
| `mtgdb/ui/workspace.py` | change autosave scheduling, detached snapshot submission, or workspace/session coordination<br>change writer shutdown/flush, retained Mainboard/Sideboard sash capture/restore, or restored-result selection coordination | `mtgdb/workspace/repository.py`; `mtgdb/deck/sessions.py`; `mtgdb/ui/search.py` | Do not know individual Search filter controls or implement JSON persistence. |
| `mtgdb/ui/database_sync.py` | change database refresh scheduling/prompt/progress presentation<br>change Tk polling, completion reconciliation, sync error, or shutdown adaptation | `mtgdb/database/sync.py`; `mtgdb/search/controller.py`; `mtgdb/ui/search.py` | Download/import logic and worker lifecycle stay in the Tk-free sync service. |
| `mtgdb/ui/printing.py` | change print destination selection or progress-dialog presentation<br>change Tk polling, completion/error messages, or print shutdown adaptation | `mtgdb/printing/service.py`; `mtgdb/printing/renderer.py` | Rendering, downloading, caching, and worker lifecycle stay outside UI. |
| `mtgdb/ui/mana.py` | change mana pip/symbol drawing or fallback symbols<br>change bundled mana-symbol loading, mana-cost Tk composition, or symbol caches | `mtgdb/ui/assets.py`; `assets/mana/*`; `mtgdb/ui/tokens.py` | Do not add general card-image downloading or deck-analysis rules here. |
| `mtgdb/ui/window.py` | change popup/window geometry, scrolling, or icon behavior<br>change dark titlebar handling, Tk scaling/DPI adaptation, or general window services | `mtgdb/ui/tokens.py`; `mtgdb/ui/assets.py`; `mtgdb/ui/app.py` | Feature layouts and feature-specific dialogs stay with their feature modules. |

## Import allow/deny matrix

Read this before adding any import. It consolidates every cross-boundary rule.
The **MAY import** column lists allowed project-layer and relevant third-party
dependencies; Python standard-library imports and sibling modules within
`mtgdb/ui/` are allowed unless a row explicitly forbids them. More-specific
rows override broader rows.

| Layer / module | MAY import | MUST NOT import |
| --- | --- | --- |
| `mtgdb/__main__.py` | `mtgdb.main` | feature internals directly; `tkinter`; `sqlite3` |
| `mtgdb/main.py` | `mtgdb.database.db`; `mtgdb.ui.app`; stdlib | `tkinter`; `sqlite3`; feature implementation modules other than the two startup owners |
| `mtgdb/ui/**` | any owning `mtgdb` API; `tkinter`; presentation-only third-party deps | `sqlite3`; raw SQL construction |
| `mtgdb/ui/search.py`, `ui/search_printings.py`, `ui/search_checklist.py`, `ui/results.py`, `ui/table_filters.py`, `ui/tables.py` | search/deck/preferences logic APIs; `mtgdb.database.constants`; sibling UI components | `sqlite3`; `mtgdb.database.db`/`CardDB` directly; SQL construction |
| `mtgdb/ui/card_detail.py` | `mtgdb.images.service`; `mtgdb.deck.legality`; `mtgdb.database.constants`; `mtgdb.core.scryfall_json`; `PIL.ImageTk` for final Tk image conversion | direct network/cache-file/worker operations; image decoding owned by the image service; legality-payload normalization owned by `deck/legality.py` |
| `mtgdb/ui/comparison.py` | `mtgdb.images.service`; `mtgdb.comparison.models`; `PIL.ImageTk` for final Tk image conversion | direct network/cache-file/worker operations; image decoding owned by the image service |
| all non-UI packages (`core`, `search`, `deck`, `database`, `comparison`, `images`, `printing`, `workspace`, `preferences`) | Tk-free logic permitted by rows below | `tkinter`; any `mtgdb.ui.*` module |
| `mtgdb/core/**` | stdlib; third-party runtime deps | any feature package (`search`, `deck`, `database`, …); `tkinter` |
| `mtgdb/core/net.py` | stdlib/HTTP | `tkinter`; `sqlite3`; `mtgdb.database.*` |
| `mtgdb/search/*` | sibling search modules; `mtgdb.database.*`; `mtgdb.core.*` | `tkinter`; `mtgdb.ui.*` |
| `mtgdb/deck/*` | sibling deck modules for delegation; `mtgdb.core.*` | `tkinter`; `sqlite3`; any `mtgdb.ui.*` |
| `mtgdb/database/constants.py` | stdlib only | `tkinter`; `mtgdb.search.*`; `mtgdb.ui.*`; `mtgdb.core.net` |
| `mtgdb/database/db.py` | `mtgdb.database.{schema,queries,search_queries,taxonomy,semantics,bulk_import}` | `tkinter`; `mtgdb.core.net`; `mtgdb.search.*`; `mtgdb.ui.*` |
| `mtgdb/database/{schema,queries,search_queries,taxonomy,semantics,bulk_import}.py` | `sqlite3`; `mtgdb.database.{constants,semantics,schema}` | `tkinter`; `mtgdb.core.net`; `mtgdb.search.*`; `mtgdb.ui.*`; `mtgdb.database.db` |
| `mtgdb/database/sync.py` | `mtgdb.core.{net,background_jobs}`; `mtgdb.database.{bulk_import,schema}` | `tkinter`; `mtgdb.search.*`; `mtgdb.ui.*`; `mtgdb.database.db` |
| `mtgdb/core/scryfall_json.py` | stdlib | `tkinter`; `sqlite3`; `PIL`; any `mtgdb.*` module |
| `mtgdb/comparison/models.py` | stdlib; `mtgdb.core.scryfall_json` | `tkinter`; `sqlite3`; `mtgdb.core.net`; any `mtgdb.ui.*` |
| `mtgdb/images/service.py` | `mtgdb.core.{net,cache_names,background_jobs,scryfall_json}`; `PIL` | `tkinter`; `mtgdb.ui.*` |
| `mtgdb/printing/renderer.py` | `reportlab`; stdlib | `PIL`; `tkinter`; `mtgdb.core.net`; `mtgdb.core.cache_names`; deck state; worker management |
| `mtgdb/printing/service.py` | `mtgdb.core.{net,cache_names,background_jobs}`; `mtgdb.printing.renderer`; `PIL` | `tkinter`; `mtgdb.ui.*` |
| `mtgdb/workspace/repository.py` | stdlib; `mtgdb.core.background_jobs`; `mtgdb.deck.{model,sessions}` | `tkinter`; `sqlite3`; `mtgdb.ui.*`; `mtgdb.database.*` |
| `mtgdb/preferences/repository.py` | stdlib | `tkinter`; `sqlite3`; `mtgdb.ui.*` |

- **LAYER-001 — MUST:** Import `tkinter` (or build a Tk widget/image) only from
  modules under `mtgdb/ui/`. No module outside `mtgdb/ui/` imports `tkinter`.
  _Verification:_ **AUTO**.
- **LAYER-002 — MUST NOT:** Import any `mtgdb.ui.*` module from a non-UI
  package. _Verification:_ **AUTO**.
- **LAYER-003 — MUST NOT:** Import `sqlite3` or construct SQL in any `mtgdb.ui.*`
  module. _Verification:_ **AUTO**.
- **LAYER-004 — MUST:** Obey the specific MAY/MUST NOT rows above for every
  added import. _Verification:_ **AUTO**.
- **LAYER-005 — MUST NOT:** Import a feature package from `mtgdb/core/**`; core
  is the shared base and depends on nothing above it. _Verification:_ **AUTO**.

## Naming and placement

- **NAM-001 — MUST:** Place a new module in the package that owns its layer and
  domain: presentation under `mtgdb/ui/`, shared domain-agnostic code under
  `mtgdb/core/`, and each other concern under its domain package.
  _Verification:_ **REVIEW**.
- **NAM-002 — MUST:** Let the folder convey layer/domain; do not re-encode it in
  the filename (`ui/tables.py`, not `ui/ui_tables.py`). _Verification:_ **AUTO**.
- **NAM-003 — MUST NOT:** Name a module with a Python reserved word or a stdlib
  top-level name that it would shadow within its package. _Verification:_ **AUTO**.
- **NAM-004 — MUST:** Give each module a single exclusive responsibility and add
  it to both the Module ownership table and Module map in the same change. _Verification:_ **AUTO**.
- **NAM-005 — MUST NOT:** Duplicate an existing component, token, style, parser,
  helper, taxonomy rule, storage rule, or worker pattern; extend the owning
  module instead. _Verification:_ **REVIEW**.
- **NAM-006 — MUST NOT:** Add a compatibility re-export shim module; import the
  owning module directly. _Verification:_ **AUTO**.

## Required change method

- **CHG-001 — MUST:** Inspect the current implementation and the tests that
  exercise the behavior before editing it. _Verification:_ **REVIEW**.
- **CHG-002 — MUST:** Identify the owning module from the Feature map, Module ownership, and Module
  map before editing production code. _Verification:_ **REVIEW**.
- **CHG-003 — MUST:** Limit production edits to the owning module(s), their
  shared dependency layer, directly affected tests, and required build config.
  _Verification:_ **REVIEW**.
- **CHG-004 — MUST NOT:** Perform unrelated rewrites, broad renames, formatting
  sweeps, or cleanup during a focused task. _Verification:_ **REVIEW**.
- **CHG-005 — MUST:** Preserve unrelated behavior and add or update an automated
  regression whenever a durable requirement can be checked without human
  judgment. _Verification:_ **REVIEW**.
- **CHG-006 — MUST:** Preserve user-owned changes already in the working tree
  unless the user requests their removal. _Verification:_ **REVIEW**.
- **CHG-007 — MUST:** Update the Feature map, Module ownership table, Module map, and import matrix
  in the same change whenever a module gains, loses, or transfers a
  responsibility or an import boundary. _Verification:_ **AUTO**.
- **CHG-008 — MUST:** Complete every verification class governing the change
  before reporting it complete. _Verification:_ **REVIEW**.
- **CHG-009 — MUST:** When a refactor supersedes a private helper, callback,
  compatibility wrapper, widget-state cache, or duplicate implementation, remove
  the obsolete code and update tests/contracts to assert the current owner instead
  of preserving dead compatibility fossils. Public service/domain APIs and explicit
  diagnostic hooks require separate evidence before removal. _Verification:_ **REVIEW**.

## Single-document policy

- **DOC-001 — MUST:** Keep `AGENTS.md` as the one and only project
  documentation file in the source tree and every source-release archive.
  _Verification:_ **AUTO**.
- **DOC-002 — MUST:** Limit this file to current rules, architecture, ownership,
  navigation, invariants, and verification. _Verification:_ **REVIEW**.
- **DOC-003 — MUST NOT:** Turn this file into a changelog, work log, audit,
  status report, roadmap, or before/after record. _Verification:_ **AUTO**.
- **DOC-004 — MUST NOT:** Create README, CHANGELOG, ROADMAP, NOTES, TODO,
  CONTRIBUTING, nested agent instructions, or any other documentation file.
  _Verification:_ **AUTO**.
- **DOC-005 — MUST NOT:** Add project documentation with `.md`, `.markdown`,
  `.rst`, `.adoc`, `.txt`, `.pdf`, `.doc`, `.docx`, `.odt`, or `.rtf`
  extensions. `AGENTS.md` is the sole exception. _Verification:_ **AUTO**.
- **DOC-006 — MAY:** Keep comments and docstrings inside source, and let the
  running app read/write user-selected deck `.txt` files and generated PDFs.
  _Verification:_ **AUTO**.

## Search architecture

- **SRCH-001 — MUST:** Represent one semantic search with `SearchCriteria`
  before cache lookup or background execution. _Verification:_ **AUTO**.
- **SRCH-002 — MUST:** Route interactive DB access through `SearchRepository`
  and worker lifecycle through `SearchController`. _Verification:_ **AUTO**.
- **SRCH-003 — MUST NOT:** Read a Tk widget or variable from a search worker
  thread. _Verification:_ **REVIEW**.
- **SRCH-004 — MUST:** Fetch broad results via the repository's narrow
  projection and upgrade a selected/acted row by exact Scryfall ID before
  full-card behavior. _Verification:_ **AUTO**.
- **SRCH-005 — MUST:** Create search checklist dialogs in `ui/search_checklist.py`
  and table-filter popups in `ui/table_filters.py` withdrawn, fully dark-styled
  while hidden, and map them only after complete first-frame controls exist. Reuse
  high-frequency popup shells instead of exposing reconstruction between opens.
  _Verification:_ **AUTO**.
- **SRCH-006 — MUST:** Keep large picker option sets behind a fixed reusable
  `VirtualChecklistView` pool of at most 32 live choice widgets and keep Search
  Results on a fixed reusable viewport of at most 128 live Tk `Treeview` items
  regardless of logical match count. Logical option/result count MUST NOT determine
  Tcl variable or widget count. Multi-select choices use compact top-aligned
  checkboxes; single-select Format choices use compact left-aligned themed radio
  controls (UI-010).
  A scrolling checklist MUST use its available viewport height so the final visible
  choice reaches almost to the bottom edge, without enlarging the choice widgets;
  its capacity/viewport calculation MUST use the actual DPI-scaled requested choice
  row height rather than assuming the nominal 24 px minimum;
  short picker catalogs MUST instead size their viewport/window down to the visible
  choices rather than vertically stretching a few choices to fill a large selector.
  Single-select picker viewports MUST retain 5 px of extra vertical clearance for
  the bottom radio label. The shared Printings Exact Set view MAY use two virtualized
  columns while retaining the same 32-widget cap. _Verification:_ **AUTO**.
- **SRCH-007 — MUST:** Display the complete known match count independently of
  row materialization progress. _Verification:_ **AUTO**.
- **SRCH-008 — MUST:** Apply Results sorting/filtering to the full matched set,
  not only materialized rows. _Verification:_ **AUTO**.
- **SRCH-009 — MUST:** Preserve Any/All, color, numeric, Content, language,
  Format, Rarity, trusted Card Type/Supertype/Subtype/Mechanic, Paper-only,
  optional Set Type, and independent Exact Set semantics. Shared Printing Tk state
  belongs to `PrintingFilter` in `ui/set_filters.py`; `ui/search_printings.py` MUST
  only adapt that component to Search. Search Format's empty value is the explicit
  **Any** radio choice: it MUST display selected whenever no specific format is
  active, and selecting it MUST replace/unselect every specific format. An empty Set
  Type or Exact Set selection means Any. _Verification:_ **AUTO**.
- **SRCH-010 — MUST NOT:** Define `_do_search`, `_render_results`,
  `_show_table_filter`, or `_open_search_multi_picker` on `DeckBuilderApp`.
  _Verification:_ **AUTO**.
- **SRCH-011 — MUST:** Route the deck action that searches alternate printings
  through the Search feature's `_apply_cards_search_preset`; direct manipulation
  of individual search widgets or filter variables is prohibited in `ui/deck.py`.
  _Verification:_ **AUTO**.
- **SRCH-012 — MUST:** Capture and restore Search-owned workspace state in
  `ui/search.py`; `ui/workspace.py` coordinates persistence without knowledge of
  individual Search widget/catalog/set-variable names. _Verification:_ **AUTO**.
- **SRCH-013 — MUST:** Keep the complete logical Results set in
  `search/results.py` and only its fixed-slot viewport adaptation in `ui/results.py`;
  preserve selection by exact Scryfall printing ID outside Tk, never by physical
  slot or row index, and restore workspace selection only after the matching logical
  view is available. _Verification:_ **AUTO**.
- **SRCH-014 — MUST:** Make `SearchController.invalidate()` invalidate every
  queued or in-flight prior generation, allow a current-database search to start,
  and reject stale worker events without restoring their cache.
  _Verification:_ **AUTO**.
- **SRCH-015 — MUST:** Validate editable numeric Search fields before creating
  `SearchCriteria`, reject non-numeric or non-finite values and
  minimum-greater-than-maximum ranges with a clear UI error, reject non-finite
  numeric values again at the query-builder boundary, and never raise those
  validation errors through a Tk callback. _Verification:_ **AUTO**.
- **SRCH-016 — MUST:** Treat Scryfall legality statuses `legal` and `restricted`
  as playable for format Search, retain the restricted distinction in card-detail
  presentation, and reject unknown content-filter vocabulary instead of silently
  broadening a query. _Verification:_ **AUTO**.
- **SRCH-017 — MUST:** When a new Search criterion or Results field needs card
  data not already stored, inspect and update `database/schema.py` and
  `database/bulk_import.py` before Search-layer code; Search modules MUST NOT
  emulate a missing stored field or parse raw Scryfall bulk payloads.
  _Verification:_ **AUTO**.
- **SRCH-018 — MUST:** Keep the Results Treeview in native `extended` selection
  mode: Ctrl-click changes highlight membership and MUST NOT mutate comparison.
  Treat the focused highlighted row as the preview/status primary while toolbar
  and context batch actions operate on every highlighted Results card. `Add Mainboard`
  / `Add Sideboard` add the complete highlighted batch to the corresponding board;
  Results highlighting MUST remain available to the global comparison bar so one
  `Add Selected` action can combine Results with highlighted Mainboard/Sideboard
  cards subject to the seven-card limit. _Verification:_ **AUTO**.
- **SRCH-019 — MUST:** `Clear` resets every Search criterion and its visible control
  summary to the trusted defaults: Cards-only content with Tokens, Emblems, and Art
  Series off; Paper-only; Any Set Type/Set;
  no Subtype/Mechanic/Rarity/Card Type/Supertype/Color/Format/numeric restriction,
  empty Rules Text and name/exact-name batch, and English-only enabled. It MUST also
  deselect current Results/Mainboard/Sideboard highlights without removing cards
  already stored in the comparison collection. Subtype, Mechanics, Format, and
  Rarity MUST display `Any` when unrestricted. _Verification:_ **AUTO**.
- **SRCH-020 — MUST:** A deck context search for multiple highlighted rows uses one
  exact-name batch criteria snapshot containing every distinct selected card name;
  a single selected row uses the same preset path with one exact name. The Card Name field
  MAY display the batch summary, but SQL ownership remains in
  `database/search_queries.py`. _Verification:_ **AUTO**.
- **SRCH-021 — MUST:** Keep the normal Search surface focused on Card Name, Card Type,
  Supertypes, Colors, numeric ranges, and Mechanics. Place Content, Rules Text,
  Subtype, Format, Rarity, and Printings behind `Advanced Filters` in that exact order,
  with Rules Text immediately above Subtype and Format/Rarity immediately below
  Subtype. Content MUST show one compact horizontal choice group in the exact order
  `Cards | Tokens | Emblems | Art Series` on the same row as the Content label. The
  `Cards` choice MUST begin at the same shared Advanced control-column x-position as
  Rules Text, Subtype, Format, Rarity, and Printings; the Content choices MUST remain
  compact and left-grouped rather than stretching across the Search width. Rules Text,
  Subtype, and Printings MUST share the same outer two-column label/control grid so
  Format, Rarity, and the Printings picker align with Rules Text and Subtype instead of
  nesting a second label grid. Content,
  Rules Text, Subtype, Format, Rarity, and Printings labels MUST use the same normal
  body-label typography and
  text color as primary Search labels such as Card Name and Card Type; they are field
  labels, not gold section headings. There is no `More Types` or
  `Characteristics` filter; named Scryfall keyword/ability data is presented as
  `Mechanics`. _Verification:_ **AUTO**.
- **SRCH-022 — MUST:** Rebuild trusted filter vocabulary when Content or Paper-only
  scope changes and prune selected values that no longer exist in that scope.
  Workspace restore MAY select values already in the current vocabulary but MUST
  NOT add card types, Supertypes, Subtypes, Mechanics, Formats, Rarities, Set Types,
  or Sets to that vocabulary. _Verification:_ **AUTO**.

- **SRCH-023 — MUST:** Do not render a duplicate aggregate `Active Filters`
  summary above the Search actions. Mechanics and Subtype picker buttons MUST list
  selected values directly through the first ten selections and then append `+N`
  for the remainder; Rarity MUST list every selected rarity. Mechanics, Format,
  Subtype, and Rarity MUST use `Any` as their unrestricted
  button text. The Subtype picker summary MUST remain one horizontal line and
  MUST NOT insert line breaks; other picker summaries MAY wrap to preserve
  readable widths without changing the underlying selection state. Mechanics and
  Subtype picker helper text MUST be exactly `Choose one or several card mechanics.`
  and `Choose one or several card subtypes.` respectively. Shared multi-select filter
  dialogs MUST label their visible bulk-select action `Select All` and their visible
  bulk-clear action `Clear Selected`; the Exact Set bulk-select action MUST also read
  `Select All`. These label changes do not broaden the established visible/filtered
  action scope. Search Printings MUST display no introductory instruction directly
  below its title; Open Deck Printings
  MAY retain its resolver-specific introductory guidance, and Exact Set guidance/status
  below the Exact Set section remains unchanged.
  _Verification:_ **AUTO**.

- **SRCH-024 — MUST:** Search Rules Text against the normalized complete Oracle
  text of every card face. Within one unquoted Rules Text chip, every normalized
  word MUST occur but MAY have other words between it; a chip wrapped in double
  quotes MUST require that normalized phrase contiguously. Rules Text `All`/`Any`
  MUST combine complete chips, SQL wildcard characters MUST remain literal, and
  query construction MUST stay in `database/search_queries.py`.
  _Verification:_ **AUTO**.

- **SRCH-025 — MUST:** `SearchResultStore` owns every logical broad-result row and
  the current complete ordered index. Treeview slots are disposable presentation;
  there is no result cap or artificial paging, and Preview, Compare, Add Mainboard,
  and Add Sideboard hydrate exact printings only when needed. _Verification:_ **AUTO**.
- **SRCH-026 — MUST:** Prepare Results filtering/sorting and categorical column
  vocabularies off the Tk thread with latest-generation protection. The old logical
  view remains usable until an accepted replacement swaps atomically; stale worker
  output MUST NOT overwrite newer filter/sort state. _Verification:_ **AUTO**.
- **SRCH-027 — MUST:** Discover trusted Card Type/Supertype/Subtype/Mechanic/Format/
  Rarity/Set Type/Exact Set taxonomy asynchronously after first paint and on cold
  Content/Paper/Set-Type scopes. Database replacement MUST invalidate bounded
  taxonomy caches and use the same worker path. While unavailable, UI vocabulary
  remains empty/loading rather than guessed. _Verification:_ **AUTO**.
- **SRCH-028 — MUST:** Bound Search taxonomy snapshot caches and result vocabulary
  caches with LRU eviction. Cache scope MUST include every authority-changing
  Content/Paper/Set-Type input so stale vocabulary cannot cross scopes.
  _Verification:_ **AUTO**.
- **SRCH-029 — MUST:** Use an inverse logical-view index for O(1) exact-printing
  position lookup, cooperatively abort obsolete long Results preparations, and use
  row-ring rotation for small logical scroll deltas so a one-row wheel step does
  not rewrite the full physical viewport. Logical multi-selection MUST remain
  bitset/range compact for very large selections while normal sparse selection
  count/order operations scale with selected rows rather than scanning the complete
  100k logical view. _Verification:_ **AUTO**.
- **SRCH-030 — MUST:** Preserve the last complete trusted Search controls while a
  cold taxonomy scope is prepared; loading MUST disable/rebind stable controls
  rather than collapse the visible layout into temporary widget trees. A failed
  async taxonomy request MUST re-enable the last stable controls, restore stable
  Printings summary text, and discard pending restore state that could later
  overwrite new user edits.
  _Verification:_ **AUTO**.
- **SRCH-031 — MUST:** A user Search requested while trusted taxonomy is loading
  MUST be retained as one pending intent and automatically execute exactly once after
  the newest accepted catalog snapshot finishes. If an older query is still running,
  that one intent MUST remain queued until the query also reaches a terminal event.
  Repeated clicks MUST NOT queue duplicate searches, Search Clear MUST cancel that
  pending intent, and taxonomy failure MUST leave the Results header in a stable
  non-loading state rather than stranded on `Trusted filters are loading…`.
  _Verification:_ **AUTO**.

## Database internals architecture


- **DBI-001 — MUST:** Keep `CardDB` in `database/db.py` as the stable façade
  composed from `CardQueryMixin`, `CardSearchQueryMixin`, and `CardTaxonomyMixin`.
  _Verification:_ **AUTO**.
- **DBI-002 — MUST:** Keep schema version, tables, indexes, connection policy,
  query-function registration, migration, and init in `database/schema.py`, and
  make every schema migration one rollback-safe transaction.
  _Verification:_ **AUTO**.
- **DBI-003 — MUST:** Keep row projection, strict streaming bulk parsing,
  isolated replacement transactions, distinct committed-card threshold
  protection, and index rebuild in `database/bulk_import.py`; malformed JSONL,
  trailing JSON garbage, unterminated arrays, non-card records, and missing card
  identity MUST abort replacement. _Verification:_ **AUTO**.
- **DBI-004 — MUST:** Keep rules normalization, type-line repair, and type/subtype
  matching functions in `database/semantics.py` without SQLite connections or UI
  dependencies. _Verification:_ **AUTO**.
- **DBI-005 — MUST:** Keep exact-printing retrieval, resolver ranking, and name
  suggestions in `database/queries.py`; keep canonical search construction,
  parameter ordering, projection validation, `SearchQueryBuilder`, and the stable
  `CardDB.search()` mixin implementation in `database/search_queries.py`.
  _Verification:_ **AUTO**.
- **DBI-006 — MUST:** Keep set/format/rarity/type/subtype/keyword/catalog
  classification queries in `mtgdb/database/taxonomy.py`. _Verification:_ **AUTO**.
- **DBI-007 — MUST NOT:** Import `database/db.py` from any database-internals
  module or create a cycle through the façade. _Verification:_ **AUTO**.
- **DBI-008 — MUST NOT:** Put schema, connection construction, bulk extraction,
  canonical search construction, or taxonomy discovery directly in the façade.
  _Verification:_ **AUTO**.
- **DBI-009 — MUST:** Preserve schema version 10, the card-column contract,
  search indexes, WAL, query-only readers, and registered type/subtype
  functions. _Verification:_ **AUTO**.
- **DBI-010 — MUST:** Preserve exact-printing identity, search semantics, import
  resolution, catalog fallback, and public `CardDB` method signatures.
  _Verification:_ **AUTO**.

## Database synchronization architecture

- **DBS-001 — MUST:** Route automatic and manual refreshes through
  `DatabaseSyncService` and `DatabaseSyncController` in `database/sync.py`.
  _Verification:_ **AUTO**.
- **DBS-002 — MUST:** Keep Scryfall bulk/catalog retrieval, official Wizards
  Rules-page TXT discovery/strict Supertype parsing/provenance persistence, the
  48-hour due policy, phase timing, progress events, cancellation, and temp
  cleanup in `database/sync.py`.
  _Verification:_ **AUTO**.
- **DBS-003 — MUST NOT:** Import or call `core/net.py` from `database/db.py` or
  from database-refresh UI modules. _Verification:_ **AUTO**.
- **DBS-004 — MUST:** Keep `database/sync.py` Tk-free and `ui/database_sync.py`
  free of network transport and worker creation. _Verification:_ **AUTO**.
- **DBS-005 — MUST:** Permit at most one sync worker, tag events by generation,
  and coalesce display progress before Tk presentation. _Verification:_ **AUTO**.
- **DBS-006 — MUST:** Retain the old committed dataset until a replacement of at
  least 1,000 distinct rows actually present in SQLite commits with rebuilt
  indexes. _Verification:_ **AUTO**.
- **DBS-007 — MUST:** Refresh catalogs but skip bulk download/import when the
  local DB matches the current upstream bulk revision. _Verification:_ **AUTO**.
- **DBS-008 — MUST:** Remove the bulk temp file after success/failure/cancel and
  preserve the committed DB after an incomplete import. Because a terminated or
  crashed process never runs that cleanup, the sync service MUST also remove
  abandoned `scryfall_*.download` files beside the database when it is
  constructed. _Verification:_ **AUTO**.
- **DBS-009 — MUST:** Keep refresh scheduling, progress dialog, Tk polling,
  completion reconciliation, error presentation, and shutdown adaptation in
  `ui/database_sync.py`. _Verification:_ **AUTO**.
- **DBS-010 — MUST NOT:** Define sync methods on `DeckBuilderApp` or call
  `CardDB.sync` from production code. _Verification:_ **AUTO**.
- **DBS-011 — MUST:** Cooperatively cancel an incomplete refresh during shutdown
  and stop Tk polling before destroying the root. _Verification:_ **AUTO**.
- **DBS-012 — MUST:** Verify HTTP `Content-Length` when supplied, reject premature
  EOF, remove partial downloads on every failed final attempt, and use bounded
  transient retries for both bulk files and raw image bytes.
  _Verification:_ **AUTO**.
- **DBS-013 — MUST:** Treat any missing trusted Scryfall catalog or missing
  verified Wizards Supertype taxonomy as refresh-due metadata. Refresh both
  taxonomy sources on upgrade/startup and, when the bulk revision is already
  current, satisfy that repair without downloading/reimporting the card snapshot.
  A failed/unrecognized Wizards Rules refresh MUST preserve the last verified
  Supertype taxonomy; when no verified value exists, Supertypes MUST fail closed
  to no selectable vocabulary. A failed Scryfall `card-types` refresh MUST likewise
  preserve any last successful catalog. Persist the last Wizards Supertype and
  Scryfall Card Type taxonomy-refresh failures separately from verified values so
  UI code can distinguish missing authority from a valid current Content/Paper
  scope with no matching values and can surface a clear retry instruction.
  _Verification:_ **AUTO**.
- **DBS-014 — MUST:** Request Wizards Rules-page/TXT resources with document-
  appropriate HTTP media types and a browser-compatible Wizards-only User-Agent
  (while preserving the identifying application User-Agent for Scryfall), and
  discover the current Comprehensive Rules TXT only from explicit HTTPS
  `media.wizards.com` `MagicCompRules` TXT URLs present in the official Rules-page
  response. Media downloads MUST carry the official Rules page as their referrer.
  Discovery MUST support both ordinary anchor markup and URLs embedded in serialized
  page data. Explicit official asset paths containing ordinary spaces MUST be
  canonicalized to percent-encoded request URLs before transport; candidates
  containing actual C0/DEL control characters MUST be rejected. Discovery MUST
  reject ambiguous or non-Wizards candidates rather than guessing a filename.
  _Verification:_ **AUTO**.

## Deck domain architecture

- **DECK-001 — MUST:** Keep exact-printing entries, quantities, board mutations,
  totals, and entry access in `deck/model.py`; keep stored quantities strictly
  positive integers, validate board/card identity before mutation, and leave deck
  state unchanged when a mutation raises. _Verification:_ **AUTO**.
- **DECK-002 — MUST:** Keep TXT serialization, atomic temp-file/replace saving,
  section parsing, printing tags, and resolver calls in `deck/io.py`; a failed
  overwrite MUST preserve the prior user-selected deck file.
  _Verification:_ **AUTO**.
- **DECK-003 — MUST:** Keep statistics, type classification, mana pips/sources,
  probability, curves, and sample hands in `deck/analysis.py`.
  _Verification:_ **AUTO**.
- **DECK-004 — MUST:** Keep the app's verified basic format-size, sideboard,
  copy-limit, Scryfall legality-payload normalization, banned/restricted/not-legal,
  and missing-status checks in `deck/legality.py`; UI text MUST describe these as
  available/basic checks rather than a complete rules engine or definitive
  legality certification. Construction profiles MUST NOT authorize or populate
  Search Format vocabulary. _Verification:_ **AUTO**.
- **DECK-005 — MUST NOT:** Import `tkinter`, `sqlite3`, or a UI module from any
  deck module. _Verification:_ **AUTO**.
- **DECK-006 — MUST:** Preserve entries by exact Scryfall printing ID and
  aggregate legality copy limits by Oracle ID with card-name fallback. Basic-land
  exemption MUST require the actual Basic + Land type words, and Oracle text that
  explicitly permits any number or a named maximum of the card MUST override the
  ordinary four-copy/singleton ceiling without hardcoded card names.
  _Verification:_ **AUTO**.
- **DECK-007 — MUST:** Treat `[SET]`/`[SET:COLLECTOR]` TXT tags as authoritative,
  preserve saved name/format, and retain Mainboard/Sideboard/ignored sections.
  _Verification:_ **AUTO**.
- **DECK-008 — MUST:** Preserve quantity-weighted statistics, hybrid pip
  counting, produced-mana source counting, eight curve buckets, and draw
  formulas. _Verification:_ **AUTO**.
- **DECK-009 — MUST:** Keep `Deck.stats`, `Deck.to_text`, and `Deck.from_text`
  as delegation-only compatibility methods in `deck/model.py`.
  _Verification:_ **AUTO**.
- **DECK-010 — MUST NOT:** Implement deck calculations, TXT parsing, or legality
  rules in any UI module. _Verification:_ **AUTO**.
- **DECK-011 — MUST:** Apply deck-construction checks only for explicitly
  verified format profiles. Current Brawl and Competitive Brawl MUST use exactly
  100 cards, singleton construction, and no sideboard; Commander/Commander 1v1
  MUST use 100-card singleton construction; Oathbreaker MUST use 60-card singleton
  construction. A dynamically discovered format without a verified profile MUST
  report construction rules as unverified and MUST NOT inherit a guessed 60-card,
  four-copy, 15-card-sideboard profile. Missing Scryfall status for any selected
  format MUST be surfaced as incomplete card-legality verification.
  _Verification:_ **AUTO**.

## Deck editor UI architecture

- **DUI-001 — MUST:** Keep deck-pane construction, deck tabs, session switching,
  board selection/mutation callbacks, table reconciliation, and metadata
  presentation in `ui/deck.py`. _Verification:_ **AUTO**.
- **DUI-002 — MUST:** Keep stats-pane construction, curve/mana rendering, draw
  odds, sample-hand interaction, and legality presentation in `ui/deck_stats.py`.
  _Verification:_ **AUTO**.
- **DUI-003 — MUST:** Compose `DeckBuilderApp` with `DeckEditorMixin`,
  `DeckFileWorkflowMixin`, and `DeckStatsMixin`; do not redefine their owned
  methods in `ui/app.py`. _Verification:_ **AUTO**.
- **DUI-004 — MUST:** Route add/quantity/remove/board-move through the public
  `Deck` API, mark the session dirty, and reconcile only affected views before
  refreshing shared statistics. _Verification:_ **AUTO**.
- **DUI-005 — MUST:** Preserve exact-printing row identity, native highlighted-row
  selection, the focused row as the preview/odds primary, and preview stability
  during deck sorting/filtering and batch mutations. _Verification:_ **AUTO**.
- **DUI-006 — MUST:** Obtain composition metrics, curves, mana counts, draw
  probabilities, hands, and legality problems from `deck/analysis.py` or
  `deck/legality.py`, never reimplemented in a UI callback. _Verification:_ **AUTO**.
- **DUI-007 — MUST:** Keep Mainboard/Sideboard sorting and filtering independent
  and route shared schema/formatting/headings/columns through `ui/tables.py`.
  _Verification:_ **AUTO**.
- **DUI-008 — MUST:** Clear a stale sample hand after a mutation, close its
  read-only large-card window, reject a stale hidden deck-row selection, draw only
  from the current mainboard quantities, and hydrate each drawn exact printing from
  the current database before image presentation. Keep sideboard-only cards out of
  mainboard draw probabilities and sample hands. _Verification:_ **AUTO**.
- **DUI-009 — MUST NOT:** Implement deck TXT serialization, workspace
  persistence, print export, or DB schema behavior in `ui/deck.py` or
  `ui/deck_stats.py`. _Verification:_ **AUTO**.
- **DUI-010 — MUST:** Keep deck import-set selection, deck open/save dialogs,
  and JSON projection/export in `ui/deck_files.py`; keep shutdown and cross-feature
  composition in `ui/app.py`; route print export through `ui/printing.py`, workspace
  persistence through `workspace/repository.py`, Search-state adaptation through
  `ui/search.py`, and workspace/session/geometry adaptation through `ui/workspace.py`.
  _Verification:_ **AUTO**.
- **DUI-011 — MUST:** Keep mana pip drawing, bundled mana-symbol loading,
  fallback-symbol rendering, and mana-cost Tk image composition in `ui/mana.py`,
  composed through `ManaSymbolsMixin` by `DeckBuilderApp`. _Verification:_ **AUTO**.
- **DUI-012 — MUST:** Place `View hand` beside `Draw hand`, keep it disabled until
  a sample hand exists, and open the current hand through the shared fixed
  `CardComparisonWindow` read-only card-grid path without adding the hand to or
  removing anything from `ComparisonCollection`; duplicate copies in the seven-card
  hand MUST remain separately visible and MUST retain visible art even when copies
  share one image request/cache key. The inline sample-hand list MUST show each
  card name, display all seven drawn rows without its own vertical scrollbar, and rely
  on the Deck Stats pane scrollbar when more vertical room is needed. The inline Sample
  Hand MAY expose a horizontal scrollbar only while user-resized columns exceed its
  visible width, so dragged-wide Card text remains reachable without clipping; the read-only
  large-card grid MUST omit card names and the top-of-window card count. _Verification:_ **AUTO**.
- **DUI-013 — MUST:** Keep Mainboard and Sideboard Treeviews in native `extended`
  selection mode with one board owning the active highlighted batch at a time. `+`
  increments every highlighted exact printing, `–` decrements each highlighted
  quantity without deleting its final copy, and `Remove` MUST delete every
  highlighted deck entry completely regardless of quantity. After removal, if a
  visible row remains, selection MUST advance to the row now occupying the focused
  removed row's position (or the preceding final row). The board-move action moves
  the complete highlighted batch then reselects it on the destination board. A multi-row context menu says `Search for these cards` and routes every
  selected distinct name through the Search exact-name batch preset. Results,
  Mainboard, and Sideboard context menus MUST NOT expose comparison membership
  actions; the dedicated comparison bar is the only add-selected path. All deck
  mutations route through the public `Deck` API. _Verification:_ **AUTO**.

- **DUI-014 — MUST:** Present deck format as a single-choice picker button whose
  popup uses radio controls and that
  consumes the same live `_format_catalog` used by Search Format. Format labels MUST
  capitalize their first displayed letter. A saved deck format MAY remain visible
  when it is absent from the current catalog, but it MUST NOT be appended to or
  authorize the trusted Search/deck picker vocabulary. _Verification:_ **AUTO**.

- **DUI-015 — MUST:** Open Deck MUST compose the same `PrintingFilter` from
  `ui/set_filters.py` used by Search Printings instead of maintaining a second
  Paper/English/Set Type/Exact Set dialog. Open Deck's printing scope MUST remain
  Cards-only and MUST NOT expose Search-only Tokens, Emblems, or Art Series as deck
  resolver content. Untagged TXT cards MUST honor that
  selected scope through `deck/io.py` and `database/queries.py`; explicit `[SET]`
  and `[SET:COLLECTOR]` tags remain authoritative and MUST bypass picker scope.
  _Verification:_ **AUTO**.

- **DUI-016 — MUST:** Do not render a duplicate `N main · N side` count above the
  deck boards. The section headings MUST display `MAINBOARD | Cards: N` and
  `SIDEBOARD | Cards: N` using current board quantities after every deck refresh.
  _Verification:_ **AUTO**.
- **DUI-017 — MUST:** Keep user-selected file dialogs on Tk but execute deck TXT
  reading/resolution, TXT serialization/durability, and JSON export disk work on
  Tk-free deck-file workers using detached deck snapshots. _Verification:_ **AUTO**.
- **DUI-018 — MUST:** Compute the common Deck Stats aggregates from one
  deck-generation analysis snapshot rather than repeatedly sorting/traversing the
  same deck for composition, mana curve, pips, sources, and opening-land metrics.
  _Verification:_ **AUTO**.

## Workspace and deck-session architecture

- **WSP-001 — MUST:** Represent every open deck with `DeckSession` and manage the
  collection with `DeckSessionManager` in `deck/sessions.py`. _Verification:_ **AUTO**.
- **WSP-002 — MUST:** Keep `deck/sessions.py` free of Tk, SQLite, workspace file
  IO, DB access, and UI modules. _Verification:_ **AUTO**.
- **WSP-003 — MUST:** Keep versioned schema projection, payload signatures,
  atomic JSON replacement, recovery retention, and corrupt-primary fallback in
  `workspace/repository.py`; reject unsupported workspace schema versions and
  give every retained recovery snapshot a collision-safe distinct path.
  _Verification:_ **AUTO**.
- **WSP-004 — MUST NOT:** Import Tk, SQLite, a UI module, or `CardDB` from
  `workspace/repository.py`. _Verification:_ **AUTO**.
- **WSP-005 — MUST:** Keep Search widget-state capture/restore in `ui/search.py`;
  keep only the retained Mainboard/Sideboard sash capture/restore, restored-result
  selection, workspace/session coordination, and Tk autosave scheduling in
  `ui/workspace.py`. The deterministic main and center layout MUST NOT persist or
  restore obsolete main/middle sash positions.
  _Verification:_ **AUTO**.
- **WSP-006 — MUST NOT:** Open, write, replace, remove, or enumerate workspace
  files from `ui/app.py`, `ui/deck.py`, or `ui/workspace.py`. _Verification:_ **AUTO**.
- **WSP-007 — MUST:** Maintain at least one session and exactly one valid active
  index after init/append/activate/remove/restore. _Verification:_ **AUTO**.
- **WSP-008 — MUST:** Preserve each session's deck, optional path, dirty state,
  selected row, and independent Mainboard/Sideboard filters and sorts.
  _Verification:_ **AUTO**.
- **WSP-009 — MUST:** Preserve workspace schema version 1, exact printing IDs,
  names, formats, quantities, boards, active-tab index, search state, and the
  retained Mainboard/Sideboard sash geometry. Older saved main/middle sash keys
  MAY be read but MUST be ignored by the deterministic main layout.
  _Verification:_ **AUTO**.
- **WSP-010 — MUST:** Load the primary workspace first and, when missing/invalid,
  restore the newest valid recovery snapshot. _Verification:_ **AUTO**.
- **WSP-011 — MUST:** Suppress unchanged routine rewrites, retain at most eight
  recovery snapshots, and allow forced writes during shutdown; once the atomic
  primary session succeeds, a supplemental recovery-snapshot failure MUST be
  logged without converting that successful primary save into a failure.
  _Verification:_ **AUTO**.
- **WSP-012 — MUST:** Write automatic workspace data only beneath the portable
  `data` directory, replacing files atomically with collision-safe temp paths.
  _Verification:_ **AUTO**.
- **WSP-013 — MUST:** Access open-deck state through `DeckSessionManager` and
  typed `DeckSession` attributes; legacy dict/session-index collections are
  prohibited. _Verification:_ **AUTO**.
- **WSP-014 — MUST:** Tk MUST capture only a cheap detached structural workspace
  snapshot and submit it to the latest-wins workspace writer. Deep JSON-safe
  shaping, JSON serialization, temporary-file IO, `flush`/`fsync`, recovery
  history, and atomic replacement MUST execute off the Tk thread; forced shutdown
  MUST flush the newest submitted snapshot. _Verification:_ **AUTO**.
- **WSP-015 — MUST:** Workspace startup load, JSON parsing, validation, recovery
  fallback, and exact-printing hydration MUST execute off Tk. Tk applies one
  completed immutable restore result and MUST NOT enable autosave until that
  restore attempt has completed. Application shutdown MUST invalidate and join an
  active workspace loader before the shared card database is closed.
  _Verification:_ **AUTO**.

## Comparison architecture

- **CMP-001 — MUST:** Keep ordered exact-printing comparison state, limits,
  mutations, and typed results in `comparison/models.py`. _Verification:_ **AUTO**.
- **CMP-002 — MUST:** Preserve and enforce the two-card minimum and seven-card
  maximum, including constructor inputs outside that range, plus insertion order,
  exact printing identity, and separate entries for same-name different-printing
  cards. _Verification:_ **AUTO**.
- **CMP-003 — MUST:** Keep `comparison/models.py` limited to comparison state
  and the JSON list parsing the image path needs; it holds no card-attribute,
  legality, symbol, or type-line display logic. _Verification:_ **AUTO**.
- **CMP-004 — MUST NOT:** Import `tkinter`, `sqlite3`, `core/net.py`, or any
  `mtgdb.ui.*` module from `comparison/models.py`. _Verification:_ **AUTO**.
- **CMP-005 — MUST:** Keep global comparison-bar construction, mixed-source
  selection collection, context actions, mutation callbacks, status, menu rebuilding,
  and window coordination in `ui/comparison_controls.py`. _Verification:_ **AUTO**.
- **CMP-006 — MUST:** Compose `ComparisonCollection` and `ComparisonFeatureMixin`
  from `ui/app.py` without defining comparison mutation/tray/menu/window methods on
  `DeckBuilderApp`. _Verification:_ **AUTO**.
- **CMP-007 — MUST:** Read comparison cards via `ComparisonCollection.cards()`
  from `ui/comparison.py` and keep mutation out of that module.
  _Verification:_ **AUTO**.
- **CMP-008 — MUST:** Render each compared printing in `ui/comparison.py` as its
  image and contextual deck/source actions without a redundant card-name label,
  top card-count label, or generic per-card comparison-membership `Remove` button.
  Deck-origin cards display the exact originating Mainboard/Sideboard plus its live
  exact-printing quantity and expose one matching `Remove from …` action while copies
  remain; Search-origin cards expose `Add Mainboard` and `Add Sideboard`. Read-only
  card-grid views render image only, without mutation actions or card-name labels.
  Request images through the shared image service, reuse Tk PhotoImages, and reject
  stale results by generation. _Verification:_ **AUTO**.
- **CMP-009 — MUST NOT:** Add card-attribute rows, disclosure sections,
  difference highlighting, a differences-only filter, or a face-selection or any
  other dropdown to the comparison window. _Verification:_ **AUTO**.
- **CMP-010 — MUST:** Keep comparison membership separate from native Treeview
  selection. Results, Mainboard, and Sideboard MUST retain independent native
  highlights. The dedicated comparison bar lives in the deck workspace and contains
  `Add Selected`, compared-count/manage, `Compare`, and `Clear` at standard action
  density. `Add Selected` MUST gather the union of highlighted Results/Mainboard/
  Sideboard exact printings, deduplicate exact printing IDs, preserve deck-source
  provenance when available, enforce the seven-card cap, stay disabled while the
  highlighted selection cannot fit within that cap (CMP-016), and MUST NOT clear
  source highlights when cards are added. `Clear` MUST clear both comparison membership and
  current Results/Mainboard/Sideboard highlights, and MUST remain enabled when either
  source selections or compared cards exist. Preserve compared-count menu removal,
  min/max messages, insertion order, and modeless refresh. _Verification:_ **AUTO**.
- **CMP-011 — MUST:** Keep comparison/read-only card-grid windows at one
  count-independent, screen-clamped locked size using the shared 1840×1120 target.
  At standard 96-DPI-equivalent Tk scaling, comparison card art MUST use the same
  340×480 portrait target as the main Card Preview whenever the available screen can
  fit it, including a 1920×1080 desktop after screen clamping. At elevated Tk scaling,
  the layout MUST reserve enough side-rail width for every complete provenance/action
  label and proportionally reduce card art before clipping an action. Keep provenance
  and mutation actions beside each image so two rows do not consume the image-height budget;
  scale images down proportionally only when a smaller display requires it. Use at
  most four image columns and wrap five through seven cards to a second row. Add no
  horizontal or vertical scrollbar or scrolling Canvas.
  _Verification:_ **AUTO**.
- **CMP-012 — MUST:** Present comparison minimum/maximum notices through an
  app-owned dark dialog using `PALETTE` and shared button components; native
  `messagebox` dialogs are prohibited for comparison notices. _Verification:_ **AUTO**.
- **CMP-013 — MUST:** When a card is added to comparison from a deck board, keep
  its originating `DeckSession` and exact source board in
  `ui/comparison_controls.py`; do not infer provenance from another board that happens
  to contain the same printing. Report the source board's live quantity. Each source
  removal removes exactly one copy, deletes the board entry only when its quantity
  reaches zero, routes through the public `Deck` API, marks that source session dirty,
  refreshes visible views when active, and MUST NOT remove the card from comparison.
  _Verification:_ **AUTO**.
- **CMP-014 — MUST:** Reuse `CardComparisonWindow` for read-only large-card grids
  such as the sample opening hand. Static-grid cards MUST preserve list order and
  duplicate instances, use the same fixed scrollbar-free geometry/image service,
  omit card-name labels, and MUST NOT mutate or consume `ComparisonCollection`.
  The shared comparison/read-only window header MUST NOT show a card-count label and
  MUST render its title in the same gold accent/dialog-title typography used by the
  Mechanics and Subtype picker windows.
  _Verification:_ **AUTO**.
- **CMP-015 — MUST:** A compared card with no valid deck-source provenance
  offers `Add Mainboard` / `Add Sideboard` against the currently active deck. Each
  click adds exactly one copy through the public `Deck` API, marks the active session
  dirty, refreshes the affected board, and leaves the card in comparison.
  _Verification:_ **AUTO**.
- **CMP-016 — MUST:** Refuse an over-cap comparison add visibly instead of
  silently truncating it. Count the highlighted Results/Mainboard/Sideboard
  printings that are not already compared; when that count plus the compared
  count exceeds the seven-card maximum, `ui/comparison_controls.py` MUST disable
  `Add Selected`, append ` (Too Many Cards Selected)` to the `Cards Selected:`
  heading, and colour that heading palette alert red through a shared
  `ui/styles.py` label style rather than a local font or colour. Re-highlighting
  an already-compared printing MUST NOT count toward the overflow, because adding
  it again changes nothing. The alert MUST pulse between the bright and dimmed
  alert reds a bounded number of times on each crossing and then hold the bright
  red; it MUST NOT animate without end, restart while the selection stays over
  the cap, or leave a pending timer after the heading is destroyed. Returning
  within the cap MUST restore the heading's normal text and style.
  _Verification:_ **AUTO**.

## Card detail and interactive image architecture

- **IMG-001 — MUST:** Route main-preview and comparison image requests through
  the single `CardImageService` owned by the app. _Verification:_ **AUTO**.
- **IMG-002 — MUST NOT:** Import `tkinter` or construct a Tk image from
  `images/service.py`. _Verification:_ **AUTO**.
- **IMG-003 — MUST:** Limit interactive image work to four daemon workers
  (spawned via `core/background_jobs.spawn_daemon`) and logically cancel queued
  requests during shutdown. _Verification:_ **AUTO**.
- **IMG-004 — MUST:** Deduplicate identical in-flight requests, serialize disk
  writes to the same path, and maintain independent weighted LRUs for decoded source
  and processed images. _Verification:_ **AUTO**.
- **IMG-005 — MUST:** Validate downloaded images and replace cache files
  atomically through collision-safe paths from `core/cache_names.py`; never treat
  an existing unindexed cache file as belonging to a requested printing.
  _Verification:_ **AUTO**.
- **IMG-006 — MUST NOT:** Perform direct network, cache-file, PIL decoding, or
  worker-thread ops from `ui/card_detail.py` or `ui/comparison.py`.
  _Verification:_ **AUTO**.
- **IMG-007 — MUST:** Reject stale preview/comparison image results by request
  token, face selection, and comparison generation before changing a widget.
  _Verification:_ **AUTO**.
- **IMG-008 — MUST:** Preserve automatic landscape rotation policy, double-faced
  face selection, text fallback, playable-format legality interpretation, and
  ordinary-preview image size constraints. Card-preview legality MUST accept both
  stored JSON text and already decoded legality mappings through `deck/legality.py`
  rather than silently displaying no legal formats for one representation. Manual
  preview rotation MUST compose with, not replace, the automatic posture.
  _Verification:_ **AUTO**.
- **IMG-009 — MUST NOT:** Define main card-pane construction, preview, fallback,
  legality popup/presentation, or image-completion methods on `DeckBuilderApp`.
  _Verification:_ **AUTO**.
- **IMG-010 — MUST:** Remove abandoned `*.download` image-cache partials when
  the image service starts and give worker threads a bounded shutdown join without
  permitting new requests after shutdown begins. The sweep MUST recurse into
  nested cache directories: the print-template cache is a `print_png`
  subdirectory using the same partial-file convention, and its orphans carry
  distinct per-card names, so a top-level-only scan lets them accumulate
  permanently. _Verification:_ **AUTO**.
- **IMG-011 — MUST:** Expose `Legality`, `Rotate`, and `Zoom` actions in the main
  card preview. The preview surface MUST NOT reserve a persistent inline legality
  text row. `Legality` opens a small app-owned dark popup listing the previewed
  card's playable formats, including the restricted distinction; Rotate cycles
  clockwise quarter turns while preserving the card's automatic base posture and
  MUST fit every rotated main-preview image inside the fixed 340×480 preview box
  rather than widening/clipping the center column; Zoom
  opens a dark modeless viewer with bounded zoom/panning and routes every
  resize/rotation through `CardImageService`, with final `ImageTk` conversion only
  in `ui/card_detail.py`. _Verification:_ **AUTO**.
- **IMG-012 — MUST:** Keep the main Card Preview container borderless: the shared
  `Preview.TFrame` uses zero border width and flat relief, with no surrounding box
  around the Card Preview surface. _Verification:_ **AUTO**.
- **IMG-013 — MUST:** Treat the main card preview as a latest-wins image-request
  channel. A newer main-preview request MUST cancel older queued work that has no
  other consumer and MUST receive higher queue priority than unscoped background
  image work; deduplicated requests used by comparison, zoom, or another unscoped
  consumer MUST remain protected. Running worker calls MAY finish, but stale results
  MUST still be rejected by the preview request token. _Verification:_ **AUTO**.
- **IMG-014 — MUST:** Keep deferred Tk image-ready retries separate from the main
  image-event-pump scheduling slot. A deferred callback MUST clear its own `after`
  handle before re-entering image completion, new selections MUST cancel obsolete
  ready retries, and move/resize or iconic deferral MUST NOT prevent later preview
  futures from being polled. _Verification:_ **AUTO**.
- **IMG-015 — MUST:** Use a 96 MiB processed-image budget and 32 MiB decoded-source
  budget with conservative `width × height × 4` weighting, plus secondary ceilings
  of 256 processed variants and 32 decoded sources. Byte limits are authoritative.
  _Verification:_ **AUTO**.
- **IMG-016 — MUST:** An image larger than its cache budget MUST still decode, resize,
  and display successfully but MUST NOT be retained. Cache locks protect lookup, LRU
  promotion, insertion, byte accounting, and eviction only; decoding/resizing stays
  outside the lock. _Verification:_ **AUTO**.
- **IMG-017 — MUST:** Keep the UI mana-cost PhotoImage cache bounded while preserving
  identical symbol rendering. _Verification:_ **AUTO**.

## Printing workflow architecture

- **PRN-001 — MUST:** Keep physical/page geometry, grid placement, cut borders,
  page counting, and atomic PDF rendering in `printing/renderer.py`.
  _Verification:_ **AUTO**.
- **PRN-002 — MUST:** Preserve 2.5×3.5 in cards, a 3×3 grid, nine per page, the
  cutting gap, rounded hairline borders, and aspect-preserving placement.
  _Verification:_ **AUTO**.
- **PRN-003 — MUST:** Render to a `.part` path, replace the PDF only after
  complete rendering, and remove incomplete output after
  success/failure/cancel. _Verification:_ **AUTO**.
- **PRN-004 — MUST:** Keep snapshot creation, board expansion, PNG validation,
  download orchestration, render coordination, typed events, and worker lifecycle
  in `printing/service.py`. _Verification:_ **AUTO**.
- **PRN-005 — MUST:** Capture print jobs before background execution with
  mainboard before sideboard, quantity expansion intact, and every available
  exact printing ID preserved. _Verification:_ **AUTO**.
- **PRN-006 — MUST:** Use `core/cache_names.py` for print PNG paths, validate
  cached/downloaded PNGs, replace corrupt files, and keep separate files per
  exact printing. _Verification:_ **AUTO**.
- **PRN-007 — MUST:** Permit one print worker, tag events by generation, coalesce
  progress before Tk, and deliver terminal success/failure/cancel through the
  controller queue. _Verification:_ **AUTO**.
- **PRN-008 — MUST:** Cooperatively cancel incomplete print work during shutdown
  and remove partial image/PDF output. _Verification:_ **AUTO**.
- **PRN-009 — MUST:** Keep destination selection, progress dialog, Tk polling,
  completion/error presentation, and shutdown adaptation in `ui/printing.py`.
  _Verification:_ **AUTO**.
- **PRN-010 — MUST NOT:** Import network transport, cache naming, ReportLab,
  queue management, or worker construction into `ui/printing.py`.
  _Verification:_ **AUTO**.
- **PRN-011 — MUST:** Compose `PrintTemplateService`, `PrintController`, and
  `PrintingMixin` in `ui/app.py`, retain the Print Deck menu command, and delegate
  print methods off `DeckBuilderApp`. _Verification:_ **AUTO**.
- **PRN-012 — MUST NOT:** Import Tk into `printing/renderer.py` or
  `printing/service.py`, or import network transport, cache naming, deck state,
  or worker management into `printing/renderer.py`. _Verification:_ **AUTO**.

## Background-job infrastructure

- **BGJ-001 — MUST:** Keep the shared cancellation exception (`JobCancelled`),
  cooperative cancel check (`check_cancel`), daemon-thread factory
  (`spawn_daemon`), and the generation-tagged single-worker controller
  (`GenerationalWorker`) in `core/background_jobs.py`; route print and database-sync
  cooperative cancellation checks through `check_cancel`. _Verification:_ **AUTO**.
- **BGJ-002 — MUST:** Keep `core/background_jobs.py` Tk-free and free of feature
  imports; it is the shared base. _Verification:_ **AUTO**.
- **BGJ-003 — MUST:** Derive `PrintController` and `DatabaseSyncController` from
  `GenerationalWorker`, delegating generation counting, event queueing, cancel,
  and shutdown to the base. _Verification:_ **AUTO**.
- **BGJ-004 — MUST:** Subclass subsystem cancellation exceptions
  (`PrintCancelled`, `DatabaseSyncCancelled`) from `JobCancelled` so callers can
  catch either. _Verification:_ **AUTO**.
- **BGJ-005 — MUST:** Create image, print, and sync worker threads through
  `spawn_daemon`. _Verification:_ **AUTO**.
- **BGJ-006 — MUST:** Preserve the single-worker limit, generation tagging, and
  coalesced progress of printing and syncing across changes to the shared base.
  _Verification:_ **AUTO**.

## Shared table architecture

- **TBL-001 — MUST:** Define columns, default visibility, display formatting, and
  sort-key behavior in `ui/tables.py`. _Verification:_ **AUTO**.
- **TBL-002 — MUST:** Route reads and atomic writes of UI preferences through
  `UIPreferencesRepository` in `preferences/repository.py`. _Verification:_ **AUTO**.
- **TBL-003 — MUST NOT:** Import `tkinter` from `preferences/repository.py` or
  perform preference-file IO from `ui/app.py`. _Verification:_ **AUTO**.
- **TBL-004 — MUST:** Keep Results, Mainboard, and Sideboard on the single schema
  and behavior contract exported by `ui/tables.py`. _Verification:_ **AUTO**.
- **TBL-005 — MUST:** Sanitize saved layouts, reject unknown/duplicate column
  IDs, migrate older layouts, and retain at least one usable column per table.
  _Verification:_ **AUTO**.
- **TBL-006 — MUST:** Keep Cost as the protected non-draggable tree column when
  visible. _Verification:_ **AUTO**.
- **TBL-007 — MUST:** Persist visibility/reset/reorder without changing unrelated
  preference keys. One `Reset` action MUST restore default visible columns, default
  display order, and canonical width/minimum/stretch geometry for every physical
  column in that table; repeated Reset clicks MUST NOT be required.
  _Verification:_ **AUTO**.
- **TBL-008 — MUST NOT:** Define shared table formatting/sorting/heading/
  column-popup/visibility/reorder methods on `DeckBuilderApp`.
  _Verification:_ **AUTO**.
- **TBL-009 — MUST:** Keep every configurable Results card-data column
  synchronized with `SearchRepository.SEARCH_RESULT_COLUMNS`; adding or removing
  a shared card-data table field requires inspecting both `ui/tables.py` and
  `search/repository.py` so the narrow Results projection supplies the value.
  _Verification:_ **AUTO**.
- **TBL-010 — MUST:** A column-filter popup MUST expose `Clear this` as its
  scoped reset and MUST NOT expose a second all-table clear action inside the popup.
  `Clear this` removes only that column's filter. Each table header's `Clear Filters`
  action MUST remove every column filter for that owning Results/Mainboard/Sideboard
  view. The main Search-row `Clear` action MUST also clear every Results column filter
  and dismiss any open Results filter editor so no hidden column text/value filter survives
  a visible Search-form reset. _Verification:_ **AUTO**.
- **TBL-011 — MUST:** Position every Edit Columns popup inside the visible work
  area of the physical monitor containing its anchor control. Clamp both positive
  and negative virtual-desktop coordinates and prefer opening above the anchor when
  there is insufficient room below. _Verification:_ **AUTO**.

## UI component system

| Semantic role | Required use |
| --- | --- |
| `standard` / `primary` | Standard-density secondary / primary action |
| `compact` / `compact_primary` | Compact secondary / primary action |
| `deck` | Deck quantity, removal, board-move controls |
| `picker` | Button/menu button representing a form choice |
| `dense` / `dense_primary` | Reserved dense secondary / primary family; use only where an owning layout explicitly requires dense action density |

- **UI-001 — MUST:** Use `AppButton`, `AppMenubutton`, `AppEntry`, `AppCombobox`,
  and `AppSpinbox` for ttk controls they cover. _Verification:_ **AUTO**.
- **UI-002 — MUST:** Use `ClassicButton`, `ClassicEntry`, `ClassicCheckbutton`,
  and `ClassicRadiobutton` when classic Tk behavior is required. Single-select
  choice rows inside a picker list viewport are the documented exception and
  MUST follow UI-010 instead. _Verification:_ **AUTO**.
- **UI-010 — MUST:** Render every single-select choice row inside a picker list
  viewport as a themed `ttk.Radiobutton` carrying the shared
  `ListChoice.TRadiobutton` style, whose selected and unselected indicator
  colours are supplied by `ui/styles.py`. A classic Tk radio MUST NOT be used
  for those rows: its indicator is painted by the host platform, ignores this
  palette, and leaves selected and unselected rows visually identical, so every
  row reads as selected. A palette contrast assertion on `selectcolor` does not
  detect that failure; the style's own selected/unselected indicator colours
  MUST differ and MUST be asserted. `ClassicRadiobutton` stays correct for the
  dialog's own `Any`/`All` mode rows, which are not list rows.
  _Verification:_ **AUTO**.
- **UI-003 — MUST:** Use `TokenBubbleEntry` for multi-value rules-text input, the
  `ui/autocomplete.py` components for autocomplete fields, and `ToolTip` for
  application-owned tooltips. _Verification:_ **AUTO**.
- **UI-004 — MUST NOT:** Instantiate `tk.Button`, `tk.Entry`, `tk.Checkbutton`,
  or `tk.Radiobutton` outside `ui/components.py`. _Verification:_ **AUTO**.
- **UI-005 — MUST NOT:** Bypass the shared form roles with raw ttk Entry,
  Combobox, or Spinbox in feature code. _Verification:_ **AUTO**.
- **UI-006 — MUST:** Select button roles by interaction meaning, not color.
  _Verification:_ **REVIEW**.
- **UI-007 — MUST:** Add a new reusable role through tokens, styles, components,
  and contract tests when no defined role fits. _Verification:_ **AUTO**.
- **UI-008 — MUST:** Keep autocomplete popup behavior and the current
  `AutocompleteEntry` implementation in `ui/autocomplete.py`; popup lifecycle,
  keyboard navigation, positioning, and dismissal stay centralized there and
  `ui/components.py` defines no autocomplete widgets. _Verification:_ **AUTO**.
- **UI-009 — MUST:** Keep set-type humanization, the reusable set-filter dialog
  shell, and flat controls for currently observed Scryfall `set_type` values in
  `ui/set_filters.py`; it MUST NOT define recommended sets, default-on membership,
  or application-authored set families/groups. Search and deck import consume this
  shared owner instead of importing each other's feature module. _Verification:_ **AUTO**.

## Window and platform services

- **WIN-001 — MUST:** Keep popup geometry, dark title bars, global mouse-wheel
  routing, DPI/maximize, window icon, and dark-menu construction in
  `WindowServicesMixin` in `ui/window.py`. _Verification:_ **AUTO**.
- **WIN-002 — MUST:** Compose `DeckBuilderApp` with `WindowServicesMixin` and not
  redefine its methods in `ui/app.py`. _Verification:_ **AUTO**.
- **WIN-003 — MUST:** Route mouse-wheel input by the widget under the pointer.
  _Verification:_ **AUTO**.
- **WIN-004 — MUST:** Confine Windows-only platform calls (DWM dark title bar,
  DPI awareness) to guarded code paths in `ui/window.py`. _Verification:_ **REVIEW**.
- **WIN-005 — MUST:** Resolve a widget's physical-monitor work area in
  `ui/window.py`; on Windows use the nearest monitor's work rectangle and on other
  platforms use Tk virtual-root bounds as the fallback. Feature modules MUST consume
  this shared work-area service rather than implementing monitor APIs themselves.
  _Verification:_ **AUTO**.
- **WIN-006 — MUST:** Create every app-owned native `Toplevel` hidden-first, apply
  dark client/frame styling and final geometry while withdrawn, and map it only
  after its first-frame widget tree is complete. _Verification:_ **AUTO**.
- **WIN-007 — MUST NOT:** Call `update()` or `update_idletasks()` from a visible
  high-frequency `<Configure>`, sash-motion, mouse-wheel, or resize callback. A
  hidden popup geometry pass is permitted before first map. _Verification:_ **AUTO**.
- **WIN-008 — MUST:** Route root resize/move and the one retained
  Mainboard/Sideboard `PanedWindow` sash drag through one layout-motion state.
  Responsive Results repaint, stats canvas/curve redraw, and comparison/deck action
  rearrangement MUST wait for the settled-layout commit. Per-motion board-sash
  safety clamps MUST be coalesced so a fast drag cannot queue an idle callback
  backlog. _Verification:_ **AUTO**.
- **WIN-009 — MUST:** Enforce DPI-tolerant Mainboard/Sideboard minima during its
  retained sash motion and use measured widget requested widths plus expansion
  hysteresis for responsive action/chip layouts; fixed pixel breakpoints MUST NOT
  choose an arrangement that cannot fit the current DPI/font scale. Controls
  cannot oscillate, clip, or cross the retained board divider while it is dragged.
  _Verification:_ **AUTO**.

## Color, typography, sizing

- **CLR-001 — MUST:** Use `PALETTE` in `ui/tokens.py` as the only palette source.
  _Verification:_ **AUTO**.
- **CLR-002 — MUST:** Use gold `accent` backgrounds only for primary actions,
  selected filter-chip state, menu hover, and text selection, always with the
  `on_accent` dark foreground; never white/light text on gold.
  _Verification:_ **AUTO**.
- **CLR-003 — MUST:** Use a dark surface with light `text` and the same thin
  one-pixel `border` outline used by the Rules Text field for secondary button
  controls; do not use the brighter `muted` color as the normal button box. Use
  `muted` for disabled primary text and keep secondary hover backgrounds dark. Under
  the clam theme, ttk secondary button/menubutton styles MUST use a relief that
  actually paints the configured one-pixel field-style outline rather than leaving
  the border color dormant. Gold primary buttons retain an accent-colored outline.
  _Verification:_ **AUTO**.
- **CLR-004 — MUST NOT:** Hard-code `on_accent` outside `ui/tokens.py`.
  _Verification:_ **AUTO**.
- **CLR-005 — MUST:** Give shared clickable button and button-like menu controls
  bounded visual activation feedback: the pressed state is inset and a successful
  release receives a brief palette-based pulse before returning to its normal role
  styling. The feedback MUST NOT alter the control command or create timers that
  persist after the control is destroyed. _Verification:_ **AUTO**.
- **TYP-001 — MUST:** Define and consume typography through shared tokens, using
  Segoe UI at the size/weight assigned per role; no local font tuples in feature
  code. _Verification:_ **AUTO**.
- **TYP-002 — MUST:** Reserve bold for titles, headings, semantic primary
  actions, and intentional active state; regular for body and secondary actions.
  _Verification:_ **AUTO**.
- **SIZ-001 — MUST:** Give primary and secondary variants of one family the same
  font size, vertical padding, border width, and requested height.
  _Verification:_ **AUTO**.
- **SIZ-002 — MUST:** Let text buttons use content-derived width (explicit char
  width is a minimum), give form controls the shared vertical metric, and give
  fields the shared body font/border/focus/padding. _Verification:_ **AUTO**.
- **SIZ-003 — MUST:** Display every complete button label without clipping at
  100%, 125%, and 150% — including `Compare`, `Add Mainboard`, and `Add Sideboard`.
  Intentional multiline labels MUST be validated by the widest rendered line,
  and both `Remove from Mainboard` and `Remove from Sideboard` MUST fit their
  comparison side rail at all three simulated Tk scales. _Verification:_ **WINDOWS**.

## Layout and behavior invariants

- **LAY-001 — MUST:** Build the main application shell as three deterministic
  horizontal columns in this order: Search/Results, fixed center, Deck Building.
  The center column MUST use `MAIN_CENTER_COLUMN_WIDTH` (470 px) and MUST NOT expose
  a draggable main-window sash. Search and Deck MUST consume all remaining width
  proportionally using the shared 51/49 weights, with Search slightly wider. The
  center column MUST stack a fixed-height Card Preview above Deck Stats; that split
  MUST NOT be draggable. The Card Preview region MUST use
  `MAIN_CARD_PREVIEW_HEIGHT` (558 px), which leaves its complete 340×480 image box
  and Legality/Rotate/Zoom row visible. Deck Stats consumes all remaining center
  height. _Verification:_ **AUTO**.
- **LAY-002 — MUST NOT:** Move controls between rows, panes, tabs, or windows
  during a presentation-only task without user approval. _Verification:_ **USER**.
- **LAY-003 — MUST:** Keep Search/Clear and Add Mainboard/Add Sideboard on the
  established Search action row, reserving the right-side deck-add group before the
  left Search/Clear group. Comparison controls MUST NOT live on this row.
  _Verification:_ **AUTO**.
- **LAY-004 — MUST:** Place one dedicated default dark `surface` comparison bar, with no gold
  outline/box, in the deck workspace below deck name/Format and above
  Mainboard/Sideboard. It MUST show `COMPARE | Cards Selected: N` as one left-aligned
  `Section.TLabel` using the exact same ttk widget/style role and left edge as
  `MAINBOARD | Cards: N`, rather than manually imitating its font/color, plus
  standard-density `Add Selected`, compared-count/manage, `Compare`, and `Clear` controls. The action controls MUST responsively
  wrap from four columns to two columns to one column as pane width requires so
  they never clip at supported display scales. While CMP-016's over-limit alert is
  showing, that same widget MUST carry the `SectionAlert`/`SectionAlertDim` label
  styles, which differ from `Section.TLabel` only in foreground, and its longer
  wording MUST wrap onto a further line rather than clip or widen the pane at
  supported display scales. _Verification:_ **WINDOWS**.
- **LAY-005 — MUST:** Preserve deck tabs, the visible New Deck control,
  user-configurable columns and filters, and exactly one user-resizable main-workspace
  sash: the vertical Mainboard/Sideboard divider. Main Search/center/Deck and
  Card Preview/Deck Stats draggable sashes are prohibited. _Verification:_ **AUTO**.
- **LAY-006 — MUST:** Lay out primary Card Type filter chips in four columns
  by default and expand to five columns only when the five-column grid's natural
  requested width fits inside the available Card Type frame. Re-evaluate this
  presentation-only layout as the frame resizes without recreating filter state.
  _Verification:_ **AUTO**.
- **LAY-007 — MUST:** Present the primary name label as **Card Name** and let its
  editable field span the remaining Search-form width. Lay out Supertypes in five
  compact columns. Keep the shared English-only Search criterion in the top-right
  of the Printings popup rather than consuming primary Search-row width.
  _Verification:_ **AUTO**.
- **LAY-008 — MUST:** Keep the primary Search rows on one uniform vertical-spacing
  token, keep Format/Rarity inside Advanced Filters immediately below Subtype, and omit redundant pane titles
  `Card Search`, `Current Deck`, `Card Preview`, and `Deck Stats` while retaining the
  uppercase bold `MAINBOARD` and `SIDEBOARD` section labels. Results MUST present its
  count in that same gold section-heading role as `RESULTS | N Cards` (transient Search
  states MAY replace the numeric portion while work is pending). Search and
  deck-workspace outer panes and the center Card Preview/Deck Stats panes MUST use
  flat borderless dark surfaces; thin field-style outlines matching Rules Text belong
  to dark secondary buttons rather than workspace containers. Results/Mainboard/Sideboard Treeviews
  MUST NOT inherit the clam theme's light outer `Treeview.field` edge; their outer
  field edge MUST blend into the dark surface. Purposeful field, table-heading,
  scrollbar, separator, and deck-tab boundaries MAY remain. The deterministic main
  shell MUST include visible non-draggable one-pixel dividers between Search/center,
  center/Deck, and Card Preview/Deck Stats so the fixed panes remain visually distinct
  without restoring draggable sashes. Search, Clear, Add Mainboard, and Add
  Sideboard MUST use the standard action density; deck quantity/remove/move controls
  MUST use the enlarged deck-control density. _Verification:_ **AUTO**.
- **LAY-009 — MUST:** Keep Deck Stats below the fixed Card Preview and let it
  consume the center column's remaining height. Omit the old top aggregate strip
  that listed total cards, lands, spells, average mana value, and sideboard count,
  together with its separator. Deck Stats MUST be a professional vertically scrollable
  dashboard with consistent dark header bands, gold section titles, comfortable
  internal spacing, and one outer stats scrollbar rather than nested section scrollbars.
  Keep Deck Overview (composition plus color requirements/sources), Mana Curve,
  Opening Hand & Draw Odds (land consistency plus selected-card odds), Sample Hand,
  and Format Legality as clearly separated sections. The Sample Hand table MUST show
  all seven drawn rows and MUST NOT have its own vertical scrollbar; the outer Deck
  Stats canvas provides scrolling when the complete dashboard exceeds available height.
  Format Legality status text MUST state the pass/failure status only and MUST NOT
  append `see Details`; the Details button remains the explicit drill-in action. The
  `BASIC FORMAT CHECK` dialog heading MUST use the same gold accent/dialog-title
  treatment as Mechanics/Subtype picker titles. Land Consistency and Selected Card draw
  odds MUST use plain-language probability labels that state what the percentage means,
  rather than compressed abbreviations. The mana-source summary MUST put
  `Red colored Sources numbers indicate missing/light mana source support.` on its own
  line directly below the `Mana-producing cards` count. Mana Curve `By type` and `By color`
  legends MUST show numeric totals for every displayed segment.
  _Verification:_ **AUTO**.
- **BEH-001 — MUST:** Preserve every existing command callback, filter function,
  Tk variable, event binding, protocol handler, and search meaning during a
  presentation-only change, comparing manifests with the pre-change source.
  _Verification:_ **REVIEW**.
- **BEH-002 — MUST:** Keep search, DB update, image loading, and print work
  asynchronous, communicating to Tk through existing UI-thread queues and
  `after` callbacks. _Verification:_ **REVIEW**.
- **BEH-003 — MUST:** Preserve autocomplete focus, dismissal, keyboard
  navigation, and popup behavior. _Verification:_ **AUTO**.
- **BEH-004 — MUST NOT:** Rebuild unrelated panes during deck-session switching,
  and keep table sorting/filtering isolated by view. _Verification:_ **AUTO**.
- **BEH-005 — MUST NOT:** Replace a button or filter command to achieve a visual
  effect, or let a presentation-only observer modify feature state.
  _Verification:_ **REVIEW**.
- **BEH-006 — MUST:** Treat every destroyable Tk popup as a bounded lifecycle:
  before or during destruction, invalidate deferred/batched callbacks that can touch
  it and release popup-owned widget caches, variable traces, tooltip registrations,
  and global widget registries. A replacement popup MUST NOT reuse a widget object
  created under an earlier destroyed `Toplevel`. _Verification:_ **AUTO**.

- **BEH-007 — MUST:** Editable Search fields for Rules Text, Mana Value, Power,
  and Toughness MUST behave like Card Name on an outside click: clear transient
  text-selection highlighting and relinquish active focus even when the clicked
  control does not take keyboard focus; the field value itself MUST remain
  unchanged. _Verification:_ **AUTO**.

- **BEH-008 — MUST:** When a Rules Text chip is removed, collapse the chip strip
  completely when no chips remain so it cannot retain its previous requested width.
  Reset the editable entry selection, insertion cursor, and horizontal viewport after
  Tk recomputes geometry; clicking a chip close control MUST return editing focus to
  the Rules Text entry. _Verification:_ **AUTO**.

## Data, taxonomy, and printing identity

- **DATA-001 — MUST:** Preserve exact Scryfall printing identity whenever an ID
  is available, and write collector-qualified tags so duplicate/split printings
  do not collapse. _Verification:_ **AUTO**.
- **DATA-002 — MUST:** Keep legacy set-only deck tags readable and every stored
  face of a multi-face card searchable. _Verification:_ **AUTO**.
- **DATA-003 — MUST:** Keep multi-word subtype phrases atomic in exact Search SQL,
  but expose a Subtype picker value only when it appears in an authoritative
  Scryfall subtype catalog and on at least one row in the current Content/Paper
  scope. Uncataloged type-line words remain queryable by explicit non-UI callers
  but MUST NOT become picker vocabulary. _Verification:_ **AUTO**.
- **DATA-004 — MUST:** Derive Search Card Type vocabulary only by intersecting
  Scryfall's `card-types` catalog with authoritative Oracle `type_line` values
  present in the current local Content/Paper scope. Card Type occurrence/search matching
  MUST be phrase-safe and MUST NOT assume an authoritative future Card Type is one word. Missing/failed catalog data
  yields no invented fallback vocabulary; arbitrary novelty/legacy words such as
  subtype text, `Emblem`, or parody type words MUST NOT be promoted to Card Type
  choices. When the authoritative `card-types` catalog itself is unavailable, the
  UI MUST say that the Scryfall Card Type taxonomy is unavailable and direct the
  user to Database > Update Database, rather than presenting the state as a normal
  empty scope. _Verification:_ **AUTO**.
- **DATA-005 — MUST:** Present this filter as **Supertypes**. Define no
  hardcoded Supertype picker vocabulary in production taxonomy/filter source.
  Rule-specific domain logic that independently needs a named supertype MUST NOT
  authorize or populate this picker. Discover the current Comprehensive
  Rules TXT from the official Wizards Rules page, locate the uniquely numbered
  `Supertypes` section structurally, strictly extract its complete first lettered
  subrule enumeration without fuzzy guessing, persist the verified values plus
  source URL/document hash/provenance, and intersect those values only with
  authoritative Oracle `type_line` values present in the current local
  Content/Paper scope. Supertype occurrence/search matching MUST be phrase-safe and
  MUST NOT assume an official future Supertype is one word. Scryfall's broader `supertypes` catalog MUST NOT authorize
  this picker. If Rules discovery/parsing fails, retain the previous verified
  vocabulary; with no previous verified vocabulary, expose no Supertype choices.
  Derive Mechanics from observed card `keywords` intersected with Scryfall's keyword-ability,
  keyword-action, and ability-word catalogs. Every selectable value MUST occur on
  at least one currently scoped local row; missing/failed catalogs yield no
  invented fallback vocabulary. _Verification:_ **AUTO**.
- **DATA-008 — MUST:** Derive Search Content only as Cards, Tokens, Emblems, and
  Art Series from Scryfall-backed row/layout semantics. Art Series MUST use the
  internal `art` content key and the user-facing label **Art Series**, MUST be
  default-off, and MUST become Search-visible only when explicitly selected. An
  explicit Art Series Content request MUST override the legacy hidden art exclusion
  so a fully broadened Search can reach every imported row. A layout unknown to this
  build MUST remain importable and Search-visible under Cards while being internally
  classified/diagnosed as unknown;
  unknown layout semantics MUST NOT be guessed. Do not create a `Supplemental` bucket: Plane, Scheme, Dungeon,
  Conspiracy, Vanguard, Phenomenon, and other legitimate objects remain their real
  Card Types under Cards. _Verification:_ **AUTO**.
- **DATA-009 — MUST:** Derive Formats, Rarities, Set Types, and Exact Sets only
  from values observed in the current local Scryfall snapshot and current
  Content/Paper scope. Format picker membership MUST come directly from locally
  observed Scryfall `legalities` keys whose status is playable (`legal` or
  `restricted`); unknown future legality statuses MUST be preserved and diagnosed
  but MUST NOT be assumed playable; production source MUST NOT define a preferred or allowed Format
  vocabulary, and deterministic display ordering MUST NOT invent membership. Set
  Type and Exact Set remain independent optional Search criteria that intersect
  when both are selected, but the Exact Set picker vocabulary MUST cascade from
  the current Content/Paper scope plus the selected observed Set Types (or all
  observed Set Types when none are selected). Changing Content, Paper-only, or Set
  Type MUST refresh that picker in place without destroying/reopening the Printings
  popup; compatible Exact Set selections MUST survive and newly ineligible Exact
  Set selections MUST be removed. Art-Series-only Content MUST expose only locally
  observed Art Series Set Types/Exact Sets, while Cards + Art Series MUST expose
  their union. Recommended-set defaults, product families, and
  grouped set taxonomies MUST NOT affect Search results. Search Printings and Open
  Deck MUST consume the same `PrintingFilter` implementation so future Paper/Set
  Type/Exact Set behavior changes cannot drift between those workflows.
  _Verification:_ **AUTO**.
- **DATA-010 — MUST:** Treat `paper` as an explicit printing scope sourced from
  Scryfall `games`; default interactive Search to Paper-only with no Set Type or
  Exact Set restriction. Unchecking Paper-only MAY expose locally observed digital
  printings and their trusted vocabulary. _Verification:_ **AUTO**.
- **DATA-006 — MUST:** Preserve both B.F.M. physical printings and their
  reconstructed type data. _Verification:_ **AUTO**.
- **DATA-007 — MUST:** Use exact JSON membership (not substring) where stored
  data represents a collection, and deduplicate display values
  case-insensitively without altering stored source values.
  _Verification:_ **AUTO**.
- **DATA-011 — MUST:** Distinguish forbidden value whitelists from allowed semantic
  hard-coding. Card Type/Supertype/Subtype/Mechanic/Format/Set/Set-Type/Rarity
  membership MUST remain authority/data-driven. Stable application semantics such as
  WUBRG, Cards/Tokens/Emblems/Art Series, known legality meanings, known layout meanings, deck
  rules, defaults, and preferred ordering MAY be explicit when unknown upstream
  values remain importable and are never silently discarded. _Verification:_ **AUTO**.
- **DATA-012 — MUST:** After trusted catalog/card refreshes, maintain an internal
  upstream compatibility fingerprint/report covering observed layouts, legality
  statuses, dynamic taxonomy and authority gaps. New structural concepts MUST be
  preserved and diagnosed rather than guessed; diagnostics MUST NOT fail or roll back
  a successful card refresh, alter picker membership, or create normal-user UI clutter.
  Recognized catalog endpoints MUST come from the declarative authority registry;
  production MUST NOT crawl or auto-interpret arbitrary future catalog endpoints.
  _Verification:_ **AUTO**.

## Portability, storage, and Windows builds

- **PORT-001 — MUST:** Store automatic runtime data only in the local `data`
  directory beside the source tree or packaged executable; no AppData, home,
  registry, or Temp fallback. _Verification:_ **AUTO**.
- **PORT-002 — MUST:** Fail startup with a clear error when the application
  directory is not writable. _Verification:_ **AUTO**.
- **PORT-003 — MUST:** Replace Scryfall bulk data atomically and commit only
  after strict parse/download integrity checks plus the distinct-card threshold
  pass, so interrupted, truncated, corrupt, or duplicate-only input cannot replace
  the previous database. _Verification:_ **AUTO**.
- **PORT-004 — MUST:** Build the Windows application as PyInstaller ONEDIR; never
  one-file. _Verification:_ **AUTO**.
- **PORT-005 — MAY:** Read/write a path the user explicitly selects for Open,
  Save, or Export. _Verification:_ **REVIEW**.
- **PORT-007 — MUST:** Write every persisted application file through one
  durable replace pattern: a unique temporary name from `tempfile.mkstemp` in
  the destination directory, `flush` and `fsync` before closing, `os.replace`
  onto the final path, and unconditional temporary cleanup. A fixed temporary
  filename lets two writers interleave into one path, and omitting `fsync` lets
  the rename become visible before the bytes it points at, publishing an empty
  file that a tolerant reader silently treats as "no saved settings". Governs
  `workspace/repository.py`, `preferences/repository.py`, and `deck/io.py`.
  _Verification:_ **AUTO**.
- **PORT-006 — MUST:** Preserve the Windows named-mutex single-instance guard.
  `acquire_single_instance` MUST be detection-only: it reports whether this
  process claimed the mutex and MUST NOT display a dialog or otherwise block.
  The already-running notice belongs to `notify_already_running`, called from
  `main`, so an automated, headless, or packaged-smoke caller observes a
  refusal instead of hanging forever on a modal dialog nothing can dismiss.
  _Verification:_ **WINDOWS**.
- **BLD-001 — MUST:** Keep `pyproject.toml` as the only dependency manifest, with
  runtime deps in `[project].dependencies` and PyInstaller in
  `[project.optional-dependencies].build`; never create `requirements.txt`.
  _Verification:_ **AUTO**.
- **BLD-002 — MUST:** Discover the `mtgdb` package in `pyproject.toml` and keep
  `MTGDeckBuilder.spec` as the authoritative ONEDIR layout and asset list, with
  `mtgdb/main.py` as the entry script. _Verification:_ **AUTO**.
- **BLD-003 — MUST:** Update `pyproject.toml`, `build_windows.bat`, and the
  GitHub workflow together when dependency or entry behavior changes.
  _Verification:_ **REVIEW**.
- **BLD-004 — MUST:** Run compilation, every cross-platform test, the Windows
  simulated-Tk-scaling geometry test at 100/125/150%, the single-instance test,
  and the full `DeckBuilderApp` startup smoke test before a Windows build
  succeeds, then run the packaged smoke test after PyInstaller; a smoke failure
  fails the build and blocks cloud upload.
  The geometry gate does not certify Windows OS or per-monitor DPI behavior.
  _Verification:_ **WINDOWS**.
- **BLD-007 — MUST:** Ship the packaged build as the program only. The
  packaged smoke test MUST remove `dist/MTGDeckBuilder/data` after it runs and
  the cloud artifact step MUST exclude that directory, so no card database,
  cached image, log, or saved workspace session is ever published inside the
  application artifact. _Verification:_ **AUTO**.
- **BLD-008 — MUST:** Keep the packaged smoke test hermetic. Seed a database the
  sync service already considers current so launching the packaged app performs
  no network refresh, and fail the test if a bulk download begins. A smoke test
  that downloads the full Scryfall export makes every build slow, network
  dependent, and unable to run offline. _Verification:_ **AUTO**.
- **BLD-009 — MUST:** Give the built `.exe` Windows version metadata whose
  `FileVersion`/`ProductVersion` are read from `pyproject.toml` at build time,
  so the shipped binary identifies its own version and cannot drift from the
  manifest. Keep UPX compression disabled. _Verification:_ **AUTO**.
- **BLD-005 — MUST:** Remove generated `*.egg-info` metadata after dependency
  install and before guardrails run. _Verification:_ **AUTO**.
- **BLD-006 — MUST:** Require Python 3.11 or newer in `pyproject.toml` and local
  build instructions, and keep CI on a Python version satisfying that minimum.
  _Verification:_ **AUTO**.

## Verification and release enforcement

- **VER-001 — MUST:** Run
  `python -m compileall -q mtgdb tests windows_tests package_release.py` before
  delivering code. _Verification:_ **AUTO**.
- **VER-002 — MUST:** Run every `tests/test_*.py` file directly before delivering
  code or creating a source release. _Verification:_ **AUTO**.
- **VER-003 — MUST:** Run `tests/test_ui_component_contract.py` after a shared
  component or ownership change. _Verification:_ **AUTO**.
- **VER-004 — MUST:** Run `tests/test_ui_visual_contract.py` after a UI token,
  style, component, or layout change. _Verification:_ **AUTO**.
- **VER-005 — MUST:** Run `tests/test_project_guardrails.py` after changing this
  file, dependency metadata, build orchestration, tests, or packaging.
  _Verification:_ **AUTO**.
- **VER-010 — MUST:** Verify a computed value against an independent expected
  value: a literal, or a closed form written out in the test. A gate MUST NOT
  compare the result of the function under test against another call to that
  same function, because both sides then move together and the assertion passes
  for every possible defect. This is how an off-by-one in the opening-hand
  probability summation survived a check labelled "probability calculations
  retain exact formulas". _Verification:_ **REVIEW**.
- **VER-011 — MUST:** Remove every temporary directory a test creates, using
  `tempfile.TemporaryDirectory` or a registered cleanup for the bare
  `tempfile.mkdtemp` form. The suite runs on every local build, every cloud
  build, and every source release, so an unmanaged directory leaks a database
  per run. _Verification:_ **AUTO**.
- **VER-012 — MUST:** Gate each validation guard with adversarial input, not
  only with its happy path. A guard that no test attempts to defeat can be
  deleted without any gate objecting; the card-search projection allow-list,
  which is the only barrier between caller-supplied column names and the
  interpolated `SELECT` list, MUST be exercised with rejected input.
  _Verification:_ **AUTO**.
- **VER-009 — MUST:** Keep `tests/taxonomy_audit.py --current` capable of
  discovering Scryfall's current `default_cards` bulk export from `/bulk-data`,
  refreshing every trusted Scryfall catalog plus the current official Wizards
  Comprehensive Rules Supertype taxonomy, auditing the full snapshot, and
  emitting a Markdown evidence report. Run it in the scheduled/manual
  `taxonomy-audit.yml` workflow; it is intentionally separate from normal local
  release gates because it downloads the full upstream dataset. _Verification:_ **AUTO**.
- **VER-006 — MUST:** Run the Windows simulated-Tk-scaling geometry test at
  100/125/150% after a control, font, spacing, or layout change; report it only as
  Tk geometry coverage, not OS/per-monitor DPI certification.
  _Verification:_ **WINDOWS**.
- **VER-007 — MUST:** Manually inspect every affected screen on Windows after a
  visual change whose behavior cannot be established by geometry and contract
  tests. _Verification:_ **MANUAL**.
- **VER-008 — MUST:** Report each unexecuted Windows or manual check as
  unexecuted; never imply full visual certification. _Verification:_ **REVIEW**.
- **REL-001 — MUST:** Create source releases only with
  `python package_release.py <output.zip>` from the project root, which runs
  compilation and every cross-platform test before writing or replacing an
  archive and exits nonzero on any failed mandatory gate before the target is
  replaced. _Verification:_ **AUTO**.
- **REL-002 — MUST:** Include source (`mtgdb/`), build config, assets, tests,
  `pyproject.toml`, `.gitignore`, `.gitattributes`, and `AGENTS.md` in a source
  release, with `AGENTS.md` the only documentation member.
  _Verification:_ **AUTO**.
- **REL-003 — MUST NOT:** Include `requirements.txt`, another documentation file,
  `__pycache__`, `.pyc`, `.pytest_cache`, `build`, `dist`, `build-venv`, local
  `data`, logs, databases, cached images, generated PDFs, or another ZIP.
  _Verification:_ **AUTO**.
- **REL-007 — MUST:** Enforce a membership ceiling as well as a floor.
  `REQUIRED_RELEASE_MEMBERS` proves a release is complete; it does not prove it
  is clean. `package_release.py` MUST additionally reject any root-level file
  outside `ALLOWED_ROOT_FILES` and any member outside
  `ALLOWED_TOP_LEVEL_DIRECTORIES`, so a scratch or debug script left in the
  project root cannot ship. _Verification:_ **AUTO**.
- **REL-004 — MUST:** Test the completed ZIP for corruption, require its members
  to exactly equal the computed source-release member set, and use the stable
  `MTG_Deck_Builder/` archive root before replacing the requested output path.
  _Verification:_ **AUTO**.
- **REL-005 — MUST:** Keep all Windows build and smoke gates before the cloud
  artifact-upload step and make every gate release-blocking. _Verification:_ **AUTO**.
- **REL-006 — MUST:** Finish a task only after confirming the owning-module
  boundary, regression coverage, behavior preservation, truthful test status,
  single-document policy, and release-gate result. _Verification:_ **REVIEW**.

## Rule → enforcing test map

Each AUTO rule group is enforced by the listed test. When you change a rule or
the behavior it governs, update its test in the same change (CHG-007).

| Rule group | Enforcing test |
| --- | --- |
| LAYER-*, NAM-*, DOC-*, CHG-007, import matrix, ownership/feature/module-map tables, build/release wiring | `tests/test_project_guardrails.py`, `tests/test_agent_routing_contract.py` |
| SRCH-* | `tests/test_search_architecture.py`, `tests/test_performance_architecture.py`, `tests/test_trusted_filter_contract.py`, `tests/test_search_printings_cascade.py`, `tests/test_future_magic.py`, `tests/test_hardening_regressions.py`, `tests/test_multiselect_interactions.py` |
| DBI-* | `tests/test_database_internals_architecture.py`, `tests/test_integrity_regressions.py`, `tests/test_hardening_regressions.py` |
| DBS-* | `tests/test_database_sync_architecture.py`, `tests/test_hardening_regressions.py` |
| VER-010 through VER-012 | `tests/test_project_guardrails.py`, `tests/test_deck_architecture.py`, `tests/test_database_internals_architecture.py` |
| DECK-* | `tests/test_deck_architecture.py`, `tests/test_integrity_regressions.py`, `tests/test_hardening_regressions.py` |
| DUI-*, TBL-* (deck side) | `tests/test_deck_ui_architecture.py`, `tests/test_multiselect_interactions.py`, `tests/test_open_deck_printings_shared.py` |
| WSP-* | `tests/test_workspace_architecture.py`, `tests/test_performance_architecture.py`, `tests/test_integrity_regressions.py`, `tests/test_hardening_regressions.py` |
| CMP-* | `tests/test_comparison_architecture.py`, `tests/test_hardening_regressions.py`, `tests/test_multiselect_interactions.py` |
| IMG-* | `tests/test_image_architecture.py`, `tests/test_performance_architecture.py`, `tests/test_preview_pipeline_regressions.py`, `tests/test_integrity_regressions.py`, `tests/test_hardening_regressions.py`, `tests/test_ui_visual_contract.py` |
| PRN-* | `tests/test_printing_architecture.py` |
| BGJ-* | `tests/test_background_jobs_architecture.py` |
| TBL-* | `tests/test_table_architecture.py` |
| UI-001 through UI-008, WIN-* | `tests/test_ui_component_contract.py`, `tests/test_ui_visual_contract.py` |
| UI-009 | `tests/test_project_guardrails.py`, `tests/test_trusted_filter_contract.py` |
| UI-010 | `tests/test_ui_component_contract.py`, `tests/test_ui_visual_contract.py`, `tests/test_search_architecture.py` |
| CLR-*, TYP-*, SIZ-*, LAY-* | `tests/test_ui_visual_contract.py`, `windows_tests/test_ui_geometry_windows.py` |
| BEH-*, callback safety | `tests/test_ui_callback_safety.py`, `tests/test_ui_component_contract.py` |
| DATA-*, VER-009 | `tests/test_trusted_filter_contract.py`, `tests/test_search_printings_cascade.py`, `tests/test_taxonomy_printings.py`, `tests/test_future_magic.py`, `tests/test_card_comparison.py`, `tests/test_taxonomy_audit_contract.py`; full upstream evidence: `.github/workflows/taxonomy-audit.yml` → `tests/taxonomy_audit.py --current` |
| PORT-001 through PORT-005, PORT-007 | `tests/test_portability_contract.py`, `tests/test_hardening_regressions.py` |
| PORT-006, single-instance | `windows_tests/test_single_instance_windows.py`, `tests/test_portability_contract.py` |
| SIZ-003, LAY-004, VER-006 simulated Tk scaling | `windows_tests/test_ui_geometry_windows.py` |
| BLD-*, REL-* | `tests/test_project_guardrails.py`, `tests/test_hardening_regressions.py` |
| BLD-004 packaged smoke | `windows_tests/smoke_packaged_windows.py` |

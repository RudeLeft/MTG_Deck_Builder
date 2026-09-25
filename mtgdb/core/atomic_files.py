"""Shared naming for atomic writes, and the cleanup a killed process cannot do.

Every durable writer in the application follows the same shape: create a
temporary file beside the target, fsync it, then replace the target in one
step, so a failed or interrupted write can never leave a half-written file
where the real one belongs.

What that shape cannot do is clean up after a process that is killed between
the two steps. The ``finally`` block never runs, and the temporary file stays
in the folder forever -- in a portable application, that is the same folder the
user is told to copy between machines. ``database/sync.py`` learned this for
bulk downloads and sweeps them on startup; this module is that sweep, written
once for every writer that needs it.

Tk-free by contract.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path


log = logging.getLogger("mtg")

TEMP_SUFFIX = ".tmp"


def temp_prefix(name):
    """Return the temporary-file prefix used for one target file name.

    The leading dot keeps the temporary out of the way on the platforms that
    honour it, and gives the sweep below something specific to match on.
    """
    return f".{str(name)}."


def sweep_abandoned_writes(directory, *prefixes):
    """Delete temporary files left behind by an interrupted write.

    Only files matching this application's own naming are removed, and only for
    the prefixes the caller names: a shared folder may hold other programs'
    temporary files, and deleting those would be a worse bug than the one this
    fixes. Returns how many were removed.

    A prefix is matched as a prefix, not as a whole name, so a caller can name
    one exact file ("session.json") or a family of them ("session_" for the
    timestamped recovery snapshots). Those two need different boundaries. A
    family prefix already ends in its own separator ("_") and is deliberately
    matched raw, so one sweep catches every differently-timestamped snapshot's
    own uniquely-suffixed temp name. An exact file name has no such built-in
    boundary: it needs the trailing-dot boundary ``temp_prefix()`` gives that
    file's own temp name, *and* a check that nothing but ``tempfile``'s own
    random suffix (never containing a dot) follows that boundary -- the
    boundary alone still can't tell "session.json"'s temp from
    "session.json.bak"'s own temp, since "session.json.bak" is a different
    file whose name simply continues right where the boundary dot is; only the
    dot-free random suffix constraint tells them apart.
    """
    try:
        folder = Path(directory)
        names = os.listdir(folder)
    except (OSError, TypeError, ValueError):
        return 0
    clean = [str(prefix) for prefix in prefixes if str(prefix)]
    if not clean:
        return 0
    family = tuple(f".{prefix}" for prefix in clean if prefix.endswith("_"))
    exact = tuple(temp_prefix(prefix) for prefix in clean if not prefix.endswith("_"))
    removed = 0
    for name in names:
        if not name.endswith(TEMP_SUFFIX):
            continue
        stem = name[:-len(TEMP_SUFFIX)]
        matched = any(stem.startswith(prefix) for prefix in family)
        if not matched:
            for boundary in exact:
                if (stem.startswith(boundary)
                        and "." not in stem[len(boundary):]):
                    matched = True
                    break
        if not matched:
            continue
        try:
            (folder / name).unlink()
            removed += 1
        except OSError:
            # A file another process still holds open is not ours to force.
            log.debug("Could not remove abandoned temporary file %s", name)
    return removed

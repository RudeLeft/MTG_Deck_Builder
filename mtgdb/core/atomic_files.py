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
    timestamped recovery snapshots).
    """
    try:
        folder = Path(directory)
        names = os.listdir(folder)
    except (OSError, TypeError, ValueError):
        return 0
    wanted = tuple(f".{prefix}" for prefix in prefixes if str(prefix))
    if not wanted:
        return 0
    removed = 0
    for name in names:
        if not name.endswith(TEMP_SUFFIX):
            continue
        if not any(name.startswith(prefix) for prefix in wanted):
            continue
        try:
            (folder / name).unlink()
            removed += 1
        except OSError:
            # A file another process still holds open is not ours to force.
            log.debug("Could not remove abandoned temporary file %s", name)
    return removed

"""Per-album progress, so an interrupted run can be continued later."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import STAGING_PREFIX, STATE_FILE_NAME

STEPS = ("rip", "tags", "lyrics", "finish")


@dataclass
class AlbumState:
    """Which steps are finished for one album folder."""
    folder: Path
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # Kept in memory only: an album opened just to edit its tags (one in the
    # library, say) must not get a state file it would keep.
    transient: bool = False

    @property
    def path(self) -> Path:
        return self.folder / STATE_FILE_NAME

    def next_step(self, after: str | None = None) -> str | None:
        """The first open step, or the first one after *after*: the user may
        jump to a step out of order, and the guided flow goes on from there."""
        names = STEPS[STEPS.index(after) + 1:] if after in STEPS else STEPS
        for name in names:
            if self.is_open(name):
                return name
        return None

    def is_open(self, step: str) -> bool:
        return step not in self.done and step not in self.skipped

    def step_states(self) -> dict[str, str]:
        """Each step's state for a front end: "done", "skipped" or "open"."""
        return {name: "done" if name in self.done else "skipped" if name in self.skipped else "open"
                for name in STEPS}

    def mark(self, step: str, skipped: bool = False) -> None:
        """Record a step and save right away, so Ctrl+C a moment later loses nothing."""
        target = self.skipped if skipped else self.done
        other = self.done if skipped else self.skipped
        if step in other:
            other.remove(step)
        if step not in target:
            target.append(step)
        self.save()

    def save(self) -> None:
        if self.transient:
            return
        data = {"cdport": 1, "done": self.done, "skipped": self.skipped,
                "updated": datetime.now().isoformat(timespec="seconds")}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def remove(self) -> None:
        """Delete the state file: a finished album should carry no trace of cdport."""
        self.path.unlink(missing_ok=True)


def load(folder: Path) -> AlbumState:
    """Read a folder's state; a folder without one starts with the rip done
    (it was ripped before, by cdport or otherwise)."""
    path = folder / STATE_FILE_NAME
    if not path.exists():
        return AlbumState(folder, done=["rip"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        done = [s for s in data.get("done", []) if s in STEPS]
        skipped = [s for s in data.get("skipped", []) if s in STEPS]
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    return AlbumState(folder, done=done, skipped=skipped)


def unfinished_albums(work_dir: Path) -> list[AlbumState]:
    """Album folders in the working folder with steps still open."""
    found = []
    if not work_dir.is_dir():
        return found
    for child in sorted(work_dir.iterdir()):
        if child.is_dir() and (child / STATE_FILE_NAME).exists():
            try:
                album = load(child)
            except ValueError:
                continue  # reported when the user opens that folder
            if album.next_step():
                found.append(album)
    return found


def leftover_staging(work_dir: Path) -> list[Path]:
    """Staging folders of rips that were interrupted."""
    if not work_dir.is_dir():
        return []
    return sorted(p for p in work_dir.iterdir() if p.is_dir() and p.name.startswith(STAGING_PREFIX))

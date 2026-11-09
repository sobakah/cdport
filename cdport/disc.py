"""Is there an audio CD in a drive?

Only Linux has a backend so far. Elsewhere detection reports None
("cannot tell") and cyanrip is left to find the drive itself.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import config, proc

# linux/cdrom.h
CDROM_DISC_STATUS = 0x5327
CDROMEJECT = 0x5309
_DISC_STATUS = {
    0: "unknown",       # CDS_NO_INFO
    1: "empty",         # CDS_NO_DISC
    2: "open",          # CDS_TRAY_OPEN
    3: "not ready",     # CDS_DRIVE_NOT_READY
    100: "audio",       # CDS_AUDIO
    101: "data",        # CDS_DATA_1
    102: "data",        # CDS_DATA_2
    103: "data",        # CDS_XA_2_1
    104: "data",        # CDS_XA_2_2
    105: "mixed",       # CDS_MIXED (audio and data tracks)
}


@dataclass
class Drive:
    device: str
    model: str = ""
    status: str = "unknown"  # audio, mixed, data, empty, open, not ready, unknown
    audio_tracks: int | None = None

    @property
    def has_audio(self) -> bool:
        return self.status in ("audio", "mixed")

    def describe(self) -> str:
        name = f"{self.device} ({self.model})" if self.model else self.device
        if self.has_audio and self.audio_tracks:
            return f"{name}: audio CD, {self.audio_tracks} tracks"
        labels = {"audio": "audio CD", "mixed": "audio CD with data tracks", "data": "data disc",
                  "empty": "no disc", "open": "tray open", "not ready": "drive not ready",
                  "unknown": "status unknown"}
        return f"{name}: {labels.get(self.status, self.status)}"


class DriveBusy(Exception):
    """Another cdport is using the drive; the message says which process."""


def lock_dir() -> Path:
    """Where drive locks live (see config.runtime_dir)."""
    return config.runtime_dir()


def lock_path(device: str) -> Path:
    # /dev/cdrom and /dev/sr0 are the same drive.
    name = Path(os.path.realpath(device)).name if device else "default-drive"
    return lock_dir() / f"drive-{name}.lock"


@contextlib.contextmanager
def hold(device: str) -> Iterator[None]:
    """Keep other cdports off the drive while this one works with its disc.

    Held from the disc preview to the end of the rip: cyanrip -I, -f and the
    rip itself all move the drive's head, and a second process doing that
    during a rip forces re-reads (seen 2026-10-12: a preview ran during a
    rip; AccurateRip still matched, but only thanks to cyanrip's retries).
    The operating system drops the lock when the process ends in any way.
    Raises DriveBusy when another cdport holds it.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: no lock, as for staging folders
        yield
        return
    config.make_runtime_dir()
    path = lock_path(device)
    # The file is never deleted: unlinking a lock file lets a second process
    # lock a new file of the same name while the first still holds the old one.
    with open(path, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            raise DriveBusy(_busy_message(device, handle.read())) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()


def busy(device: str) -> str | None:
    """Why the drive is in use by another cdport, or None when it is free.

    Only looks: taking the lock in hold()'s way, each poll could make another
    cdport starting at that moment report this one as using the drive.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: no lock, as in hold()
        return None
    try:
        handle = open(lock_path(device), encoding="utf-8")
    except FileNotFoundError:
        return None
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return _busy_message(device, handle.read())
    return None


def _busy_message(device: str, content: str) -> str:
    pid = content.strip()
    return f"{device or 'The drive'} is in use by another cdport" + (f" (process {pid})" if pid else "")


def find_drives() -> list[Drive] | None:
    """All optical drives and their discs; None when this platform has no backend."""
    if sys.platform.startswith("linux"):
        return _linux_drives()
    return None


# --- Linux -------------------------------------------------------------------
def _linux_drives(sys_block: Path = Path("/sys/class/block")) -> list[Drive]:
    drives = []
    try:
        names = sorted(p.name for p in sys_block.glob("sr*"))
    except OSError:
        names = []
    for name in names:
        device = f"/dev/{name}"
        drive = _from_udev(device)
        if drive is None:
            # The ioctl talks to the drive; leave one another cdport rips from.
            drive = Drive(device, status="unknown" if busy(device) else _ioctl_status(device))
        drives.append(drive)
    return drives


def parse_udev_properties(text: str) -> dict[str, str]:
    props = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key.strip()] = value.strip()
    return props


def drive_from_udev(device: str, props: dict[str, str]) -> Drive:
    """Interpret udev's cdrom_id properties."""
    model = " ".join(filter(None, (props.get("ID_VENDOR", "").replace("_", " ").strip(),
                                   props.get("ID_MODEL", "").replace("_", " ").strip())))
    audio = props.get("ID_CDROM_MEDIA_TRACK_COUNT_AUDIO")
    data = props.get("ID_CDROM_MEDIA_TRACK_COUNT_DATA")
    if props.get("ID_CDROM_MEDIA") != "1":
        return Drive(device, model, "empty")
    audio_count = int(audio) if audio and audio.isdigit() else 0
    data_count = int(data) if data and data.isdigit() else 0
    if audio_count and data_count:
        status = "mixed"
    elif audio_count:
        status = "audio"
    else:
        status = "data"
    return Drive(device, model, status, audio_count or None)


def _from_udev(device: str) -> Drive | None:
    """None when udevadm is missing or knows nothing about the device."""
    result = proc.capture(["udevadm", "info", "--query=property", f"--name={device}"])
    if result is None or result.returncode != 0:
        return None
    props = parse_udev_properties(result.stdout)
    if props.get("ID_CDROM") != "1":
        return None
    return drive_from_udev(device, props)


def _ioctl_status(device: str) -> str:
    """Ask the kernel directly, for systems without udev."""
    import fcntl  # POSIX only; this module is imported on Windows too

    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return "unknown"
    try:
        code = fcntl.ioctl(fd, CDROM_DISC_STATUS, 0)
    except OSError:
        return "unknown"
    finally:
        os.close(fd)
    return _DISC_STATUS.get(code, "unknown")


# Right after cyanrip closes the drive, udev or udisks may still have it open
# to look at the disc, and the kernel refuses to eject while anyone else
# does (EBUSY). Seen 2026-10-14: the first fast rip's disc stayed in.
EJECT_ATTEMPTS = 5
EJECT_WAIT_SECONDS = 1.0


def eject(device: str) -> str | None:
    """Open the tray; None when it worked, else why not.

    A fast rip runs without cyanrip's -Q, since a secure re-rip of some
    tracks may follow; this ejects once the rip is checked. It retries
    while the drive is busy, then hands over to util-linux's eject, which
    also tries a SCSI command, as cyanrip's libcdio does.
    """
    if not sys.platform.startswith("linux"):
        return "ejecting is only built for Linux"
    problem = ""
    for attempt in range(EJECT_ATTEMPTS):
        code, problem = _ioctl_eject(device)
        if code == 0:
            return None
        if code != errno.EBUSY:
            break
        if attempt < EJECT_ATTEMPTS - 1:
            time.sleep(EJECT_WAIT_SECONDS)
    if shutil.which("eject"):
        result = proc.capture(["eject", device], timeout=30)
        if result is not None and result.returncode == 0:
            return None
        detail = (result.stderr.strip() or f"exit code {result.returncode}") if result else "did not finish"
        problem += f"; eject: {detail}"
    return problem


def _ioctl_eject(device: str) -> tuple[int, str]:
    """(0, "") on success, else (errno, message)."""
    import fcntl

    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        return exc.errno or -1, exc.strerror or str(exc)
    try:
        fcntl.ioctl(fd, CDROMEJECT, 0)
    except OSError as exc:
        return exc.errno or -1, exc.strerror or str(exc)
    finally:
        os.close(fd)
    return 0, ""

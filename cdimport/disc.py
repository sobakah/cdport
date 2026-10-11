"""Is there an audio CD in a drive?

Only Linux has a backend so far. Elsewhere detection reports None
("cannot tell") and cyanrip is left to find the drive itself.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import proc

# linux/cdrom.h
CDROM_DISC_STATUS = 0x5327
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
            drive = Drive(device, status=_ioctl_status(device))
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

"""Tests for cdimport. No network, no drive, no real external tools."""

import copy
import os
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cdimport import app, cli, config, cover, covertool, disc, install, library, lyrics, musicbrainz, proc, rip, state, tags, tools, ui  # noqa: E402
from cdimport.config import STAGING_PREFIX, STATE_FILE_NAME  # noqa: E402


@pytest.fixture(autouse=True)
def private_dirs(tmp_path, tmp_path_factory, monkeypatch):
    """Keep the update-check cache, config search and drive locks away from
    the real home, so a test run never sees (or blocks) a real rip."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    # Not inside tmp_path: some tests expect it to hold only what they made.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path_factory.mktemp("runtime")))


# Real programs a test must never start: they read the drive, eject it, go
# online or change the system. Test doubles are started by path, not by name.
REAL_TOOLS = {"cyanrip", "eject", "udevadm", "lyrembed", "flatpak", "kid3", "picard", "git",
              "dnf", "sudo", "apt", "meson", "ninja", "pipx", "pkg-config"}


@pytest.fixture(autouse=True)
def no_real_tools(monkeypatch, tmp_path_factory):
    """Fail a test that would run one of the real tools (seen 2026-10-15: a
    fake that no longer matched let cyanrip open the drive)."""
    real_popen = subprocess.Popen
    test_dirs = tmp_path_factory.getbasetemp().resolve()

    class GuardedPopen(real_popen):
        def __init__(self, args, *rest, **kwargs):
            program = Path(str(args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]))
            if program.name in REAL_TOOLS and not program.resolve().is_relative_to(test_dirs):
                raise AssertionError(f"test tried to run the real {program}: {args}")
            super().__init__(args, *rest, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)


def default_cfg():
    return copy.deepcopy(config.DEFAULT_CONFIG)


def make_flac(path: Path, **tag_values: str) -> Path:
    """A header-only FLAC file mutagen can tag."""
    import mutagen.flac

    streaminfo = (4096).to_bytes(2, "big") * 2 + b"\0" * 6
    packed = (44100 << 44) | (1 << 41) | (15 << 36)  # rate, channels-1, bits-1, 0 samples
    streaminfo += packed.to_bytes(8, "big") + b"\0" * 16
    path.write_bytes(b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big") + streaminfo)
    audio = mutagen.flac.FLAC(path)
    for key, value in tag_values.items():
        audio[key] = value
    audio.save()
    return path


def feed_input(monkeypatch, answers):
    """Answer prompts in order; running out fails the test instead of hanging."""
    answers = list(answers)

    def fake_input(prompt=""):
        if not answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return answers.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return answers


# --- config ------------------------------------------------------------------
def test_partial_config_keeps_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"work_dir": "~/Rips", "cyanrip": {"offset": 6}}', encoding="utf-8")
    cfg, warnings = config.load_config(path)
    assert warnings == []
    assert cfg["work_dir"] == "~/Rips"
    assert cfg["cyanrip"]["offset"] == 6
    assert cfg["cyanrip"]["eject"] is True
    assert config.DEFAULT_CONFIG["cyanrip"]["offset"] is None  # defaults not mutated


@pytest.mark.parametrize("content, fragment", [
    ('{"cyanrip": {"offset": "6"}}', "cyanrip.offset"),
    ('{"cyanrip": {"extra_args": "-H"}}', "cyanrip.extra_args"),
    ('{"lyrics_mode": "sometimes"}', "lyrics_mode"),
    ('{"editor": "audacity"}', "editor"),
    ('{"workdir": "~/x"}', "unknown setting 'workdir'"),
    ('{"cyanrip": {"dir_scheme": "/music/{album}"}}', "dir_scheme"),
    ('{"library_layout": "{artist}/{folder}"}', "library_layout"),
    ('{"editors": {"mine": {"commands": "tageditor"}}}', "editors.mine.commands"),
])
def test_invalid_explicit_config_is_fatal(tmp_path, content, fragment):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(config.ConfigError, match=fragment.replace("{", r"\{").replace(".", r"\.")):
        config.load_config(path)


def test_broken_default_config_warns_and_uses_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "default_config_path", lambda: path)
    cfg, warnings = config.load_config()
    assert warnings and "using the defaults" in warnings[0]
    assert cfg == config.DEFAULT_CONFIG


def test_bad_settings_fall_back_alone(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "work_dir": "~/Rips", "library_dir": "~/Musik", "lyrics_mode": "sometimes",
        "cyanrip": {"offset": 6, "mode": "quick", "eject": "yes"},
        "editors": {"mine": {"commands": "tageditor"}, "named": {"name": 3, "commands": [["x"]]}},
        "editor": "mine"}), encoding="utf-8")
    monkeypatch.setattr(config, "default_config_path", lambda: path)
    cfg, warnings = config.load_config()
    # The folders and the offset still apply: no first-run questions, no new measurement.
    assert (cfg["work_dir"], cfg["library_dir"], cfg["cyanrip"]["offset"]) == ("~/Rips", "~/Musik", 6)
    assert cfg["lyrics_mode"] == "ask" and cfg["cyanrip"]["mode"] == "fast" and cfg["cyanrip"]["eject"] is True
    assert "mine" not in cfg["editors"] and "named" not in cfg["editors"] and cfg["editor"] == "eartag"
    for fragment in ("lyrics_mode", "cyanrip.mode", "cyanrip.eject", "editors.mine.commands",
                     "editors.named.name", "'editor' is 'mine'", "the others apply"):
        assert fragment in warnings[0]


@pytest.mark.parametrize("content, fragment", [
    ('{"cyanrip": {"offset": true}}', "cyanrip.offset"),
    ('{"editors": {"mine": {"name": null, "commands": [["x"]]}}}', "editors.mine.name"),
    ('{"lyrembed": {"extra_args": [1]}}', "lyrembed.extra_args"),
])
def test_more_invalid_values(tmp_path, content, fragment):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(config.ConfigError, match=fragment.replace(".", r"\.")):
        config.load_config(path)


def test_missing_explicit_config_is_fatal(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_config(tmp_path / "missing.json")


def test_user_can_add_editor_and_comments_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"_note": "mine", "editor": "puddletag",
                                "editors": {"puddletag": {"name": "puddletag", "commands": [["puddletag", "{dir}"]]}}}),
                    encoding="utf-8")
    cfg, warnings = config.load_config(path)
    assert cfg["editor"] == "puddletag"
    assert "eartag" in cfg["editors"]


def test_example_config_is_current():
    path = Path(__file__).resolve().parent.parent / "config.example.json"
    expected = config._dump(config.example_config()) + "\n"
    assert path.read_text(encoding="utf-8") == expected, (
        "config.example.json is out of date; regenerate it as described in config.write_example")


def test_copied_example_config_loads_as_defaults(tmp_path):
    source = Path(__file__).resolve().parent.parent / "config.example.json"
    copy_path = tmp_path / "config.json"
    copy_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    cfg, warnings = config.load_config(copy_path)
    assert warnings == []
    assert cfg == config.DEFAULT_CONFIG


def test_every_setting_has_help_and_no_help_is_stale():
    assert set(config.SETTING_HELP) == set(config.DEFAULT_CONFIG)
    for key, help_text in config.SETTING_HELP.items():
        if isinstance(help_text, dict):
            assert set(help_text) == set(config.DEFAULT_CONFIG[key]), key


# --- terminal output ---------------------------------------------------------
def test_marks_lists_and_menus_without_colour(monkeypatch, capsys):
    monkeypatch.setattr(ui, "_color", False)
    monkeypatch.setattr(ui, "_blank", True)
    ui.warn("first\nsecond")
    ui.check("lyrembed", "not found", False, "To install: pipx")
    ui.check("Lyrics", "skipped", None)
    ui.field("Album", "~/Musik/A")
    ui.gap()
    ui.gap()
    feed_input(monkeypatch, ["s"])
    ui.choose("Rip this disc", [("10", "a release\n      https://musicbrainz.org/release/x"), ("s", "search")])
    assert capsys.readouterr().out.split("\n") == [
        "! first",
        "  second",
        "  ! lyrembed  not found",
        "              To install: pipx",
        "  – Lyrics    skipped",
        "    Album     ~/Musik/A",
        "",                       # one gap, not two; choose() adds none after it
        "   10  a release",
        "       https://musicbrainz.org/release/x",
        "   s   search",
        "",
    ]


def test_colour_links_and_readline_safe_prompt(monkeypatch):
    monkeypatch.setattr(ui, "_color", True)
    linked = ui.links("see https://x.org/a here", "dim")
    assert "\x1b]8;;https://x.org/a\x1b\\" in linked and linked.endswith("\x1b]8;;\x1b\\ here")
    assert "\x1b[0m\x1b[2m" in linked  # the dim text after the link stays dim
    if ui._readline is not None:
        assert ui._prompt("Rip", "r").count("\x01") == 4  # every escape code is wrapped
    monkeypatch.setattr(ui, "_color", False)
    assert ui.links("https://x.org") == "https://x.org" and ui._prompt("Rip", "r") == "Rip [r]: "


def test_links_leave_sentence_punctuation_out(monkeypatch):
    monkeypatch.setattr(ui, "_color", True)
    text = ui.links("Drives are listed at https://www.accuraterip.com/driveoffsets.htm; one marked. "
                    "See https://x.org/a/b. (https://y.org/c?d=1+2)")
    assert "\033]8;;https://www.accuraterip.com/driveoffsets.htm\033\\" in text
    assert "\033]8;;https://x.org/a/b\033\\" in text and "\033]8;;https://y.org/c?d=1+2\033\\" in text


# --- disc --------------------------------------------------------------------
def test_udev_audio_disc():
    props = disc.parse_udev_properties(
        "ID_CDROM=1\nID_CDROM_MEDIA=1\nID_CDROM_MEDIA_TRACK_COUNT_AUDIO=12\nID_VENDOR=ASUS\nID_MODEL=SDRW-08D2S-U\n")
    drive = disc.drive_from_udev("/dev/sr0", props)
    assert drive.has_audio and drive.status == "audio" and drive.audio_tracks == 12
    assert "12 tracks" in drive.describe() and "ASUS" in drive.describe()


@pytest.mark.parametrize("props, status", [
    ({"ID_CDROM": "1"}, "empty"),
    ({"ID_CDROM": "1", "ID_CDROM_MEDIA": "1", "ID_CDROM_MEDIA_TRACK_COUNT_DATA": "1"}, "data"),
    ({"ID_CDROM": "1", "ID_CDROM_MEDIA": "1", "ID_CDROM_MEDIA_TRACK_COUNT_AUDIO": "10",
      "ID_CDROM_MEDIA_TRACK_COUNT_DATA": "1"}, "mixed"),
])
def test_udev_other_states(props, status):
    assert disc.drive_from_udev("/dev/sr0", props).status == status


def test_linux_drives_falls_back_to_ioctl_without_udevadm(tmp_path, monkeypatch):
    (tmp_path / "sr0").mkdir()
    (tmp_path / "sda").mkdir()
    monkeypatch.setattr(disc.proc, "capture", lambda cmd, timeout=10: None)
    monkeypatch.setattr(disc, "_ioctl_status", lambda device: "audio")
    drives = disc._linux_drives(tmp_path)
    assert [(d.device, d.status) for d in drives] == [("/dev/sr0", "audio")]


def test_linux_drives_uses_udev(tmp_path, monkeypatch):
    (tmp_path / "sr1").mkdir()
    out = "ID_CDROM=1\nID_CDROM_MEDIA=1\nID_CDROM_MEDIA_TRACK_COUNT_AUDIO=3\n"
    monkeypatch.setattr(disc.proc, "capture",
                        lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(disc, "_ioctl_status", lambda device: pytest.fail("ioctl used despite udev"))
    assert disc._linux_drives(tmp_path)[0].audio_tracks == 3


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="fcntl backend is Linux-only")
def test_ioctl_status_maps_codes(monkeypatch):
    import fcntl
    monkeypatch.setattr(disc.os, "open", lambda path, flags: 99)
    monkeypatch.setattr(disc.os, "close", lambda fd: None)
    monkeypatch.setattr(fcntl, "ioctl", lambda fd, req, arg: 105)
    assert disc._ioctl_status("/dev/sr0") == "mixed"

    def denied(path, flags):
        raise PermissionError("denied")
    monkeypatch.setattr(disc.os, "open", denied)
    assert disc._ioctl_status("/dev/sr0") == "unknown"


def test_drive_lock_keeps_a_second_cdimport_off_the_drive(tmp_path):
    assert disc.busy("/dev/sr0") is None
    with disc.hold("/dev/sr0"):
        assert disc.busy("/dev/sr0") == f"/dev/sr0 is in use by another cdimport (process {os.getpid()})"
        assert disc.busy("/dev/sr1") is None
        with pytest.raises(disc.DriveBusy):
            with disc.hold("/dev/sr0"):
                pass
    assert disc.busy("/dev/sr0") is None
    # Another name for the same drive shares its lock.
    (tmp_path / "sr7").write_text("")
    (tmp_path / "cdrom").symlink_to(tmp_path / "sr7")
    with disc.hold(str(tmp_path / "sr7")):
        assert disc.busy(str(tmp_path / "cdrom"))


def test_drive_lock_held_by_another_process(tmp_path):
    import subprocess
    child = ("import sys, time; from cdimport import disc\n"
             "with disc.hold('/dev/sr0'):\n    print('locked', flush=True); time.sleep(30)\n")
    proc_ = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True,
                             cwd=Path(__file__).resolve().parent.parent)
    try:
        assert proc_.stdout.readline().strip() == "locked"
        assert f"(process {proc_.pid})" in disc.busy("/dev/sr0")
    finally:
        proc_.kill()
        proc_.wait()
    assert disc.busy("/dev/sr0") is None  # the lock ends with the process


def test_find_disc_leaves_a_busy_drive_alone(tmp_path, monkeypatch, capsys):
    session = offset_session(tmp_path)
    drives = [disc.Drive("/dev/sr0", "ASUS", "audio", 6), disc.Drive("/dev/sr1", "LG", "audio", 10)]
    monkeypatch.setattr(app.disc, "find_drives", lambda: list(drives))
    with disc.hold("/dev/sr0"):
        assert app.find_disc(session) == "/dev/sr1"  # the only free one, no question
        drives.pop()
        feed_input(monkeypatch, ["q"])
        with pytest.raises(app.Quit):
            app.find_disc(session)
    out = capsys.readouterr().out
    assert "/dev/sr0 is in use by another cdimport" in out and "wait for a disc or a free drive" in out


def test_disc_step_holds_the_drive_until_the_rip_is_done(tmp_path, monkeypatch):
    session = offset_session(tmp_path)
    monkeypatch.setattr(app, "find_disc", lambda s: "/dev/sr0")
    seen = []

    def preview_and_rip(session, device):
        seen.append(disc.busy(device))
        return state.AlbumState(tmp_path)
    monkeypatch.setattr(app, "preview_and_rip", preview_and_rip)
    app.disc_and_rip(session)
    assert seen[0] and "another cdimport" in seen[0]
    assert disc.busy("/dev/sr0") is None


def test_eject_waits_for_a_busy_drive_then_falls_back(tmp_path, monkeypatch):
    import errno as errno_
    import fcntl
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux backend")
    device = tmp_path / "sr0"
    device.write_text("")
    answers = []

    def ioctl(fd, request, arg):
        assert request == disc.CDROMEJECT
        code = answers.pop(0)
        if code:
            raise OSError(code, os.strerror(code))
    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    monkeypatch.setattr(disc.time, "sleep", lambda s: None)
    fallback = []
    monkeypatch.setattr(disc.shutil, "which", lambda name: "/usr/bin/eject")
    monkeypatch.setattr(disc.proc, "capture", lambda cmd, timeout=10: fallback.append(cmd) or
                        SimpleNamespace(returncode=fallback_code[0], stderr="eject: unable to eject"))
    fallback_code = [0]
    answers[:] = [errno_.EBUSY, errno_.EBUSY, 0]  # busy twice (udev looking), then free
    assert disc.eject(str(device)) is None and fallback == []
    answers[:] = [errno_.EIO]  # not a busy drive: no waiting, straight to util-linux eject
    assert disc.eject(str(device)) is None and fallback == [["eject", str(device)]]
    answers[:] = [errno_.EBUSY] * disc.EJECT_ATTEMPTS
    fallback_code[0] = 1
    problem = disc.eject(str(device))
    assert "busy" in problem.lower() and "eject: unable to eject" in problem


# --- tools -------------------------------------------------------------------
def test_os_release_parsing(tmp_path):
    path = tmp_path / "os-release"
    path.write_text('# comment\nID=fedora\nID_LIKE="rhel centos"\nVERSION_ID=45\n', encoding="utf-8")
    assert tools.read_os_release(path) == {"ID": "fedora", "ID_LIKE": "rhel centos", "VERSION_ID": "45"}


def test_flatpak_command_needs_installed_app(monkeypatch):
    calls = []
    monkeypatch.setattr(tools.shutil, "which", lambda name: f"/usr/bin/{name}")

    def capture(cmd, timeout=10):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "not installed")
    monkeypatch.setattr(tools.proc, "capture", capture)
    cmd = config.DEFAULT_CONFIG["editors"]["eartag"]["commands"][0]
    assert not tools.command_available(cmd)
    assert calls == [["flatpak", "info", "app.drey.EarTag"]]
    assert tools.command_available(["kid3", "{dir}"])  # not flatpak: PATH is enough


def test_flatpak_app_id_skips_option_values():
    assert tools._flatpak_app_id(["flatpak", "run", "--arch", "x86_64", "org.musicbrainz.Picard", "{dir}"]) \
        == "org.musicbrainz.Picard"
    assert tools._flatpak_app_id(["flatpak", "run", "--arch=x86_64", "--user", "app.drey.EarTag"]) == "app.drey.EarTag"
    assert tools._flatpak_app_id(["flatpak", "run", "--branch"]) is None


def test_find_editor_takes_first_available_command(monkeypatch):
    monkeypatch.setattr(tools, "command_available", lambda cmd: cmd[0] == "flatpak")
    editor = tools.find_editor(default_cfg(), "kid3")
    assert editor.command[0] == "flatpak" and editor.name == "Kid3"


def test_executable_found_in_local_bin(tmp_path, monkeypatch):
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "cyanrip").write_text("")
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    monkeypatch.setattr(tools.Path, "home", lambda: tmp_path)
    assert tools.find_cyanrip(default_cfg()) == str(local / "cyanrip")
    cfg = default_cfg()
    cfg["cyanrip"]["path"] = str(tmp_path / "missing")
    assert tools.find_cyanrip(cfg) is None  # a configured path is not second-guessed


# --- rip ---------------------------------------------------------------------
def test_cyanrip_args_defaults():
    args = rip.cyanrip_args(default_cfg(), device="/dev/sr0")
    assert args[:2] == ["-d", "/dev/sr0"]
    assert "-Q" in args and "-W" not in args and "-K" not in args  # cyanrip's deemphasis stays on
    assert args[args.index("-D") + 1] == config.DEFAULT_CONFIG["cyanrip"]["dir_scheme"]


def test_cyanrip_args_options():
    cfg = default_cfg()
    cfg["cyanrip"].update(deemphasis=False, replaygain=False, eject=False, offset=6, dir_scheme="",
                          extra_args=["-H"])
    args = rip.cyanrip_args(cfg, release="2")
    assert args == ["-s", "6", "-R", "2", "-W", "-K", "-H"]
    info = rip.cyanrip_args(cfg, release="2", no_musicbrainz=True, info_only=True)
    assert info == ["-s", "6", "-N", "-I", "-H"]


def fake_cyanrip(files, code=0):
    """proc.run_tail stand-in that creates files relative to cwd, like cyanrip."""
    def run_tail(cmd, cwd=None, lines=3, keep_bytes=8192):
        for name in files:
            target = Path(cwd) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
        return code, []
    return run_tail


def test_rip_moves_album_out_of_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run_tail", fake_cyanrip(["Album/01 - A.flac", "Album/Album.log"]))
    folder, leftover = rip.rip("cyanrip", default_cfg(), tmp_path)
    assert folder == tmp_path / "Album"
    assert (folder / "01 - A.flac").exists() and (folder / "Album.log").exists()
    assert leftover is None
    assert not list(tmp_path.glob(STAGING_PREFIX + "*"))


def test_rip_never_overwrites_existing_album(tmp_path, monkeypatch):
    (tmp_path / "Album").mkdir()
    monkeypatch.setattr(rip.proc, "run_tail", fake_cyanrip(["Album/01.flac"]))
    folder, _ = rip.rip("cyanrip", default_cfg(), tmp_path)
    assert folder.name == "Album (2)"


def test_rip_keeps_leftovers_and_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run_tail", fake_cyanrip(["Artist/Album/01.flac", "stray.log"]))
    folder, leftover = rip.rip("cyanrip", default_cfg(), tmp_path)
    assert folder == tmp_path / "Album"
    assert leftover is not None and (leftover / "stray.log").exists()

    monkeypatch.setattr(rip.proc, "run_tail", fake_cyanrip(["Other/01.flac"], code=1))
    with pytest.raises(rip.RipError, match="exit code 1"):
        rip.rip("cyanrip", default_cfg(), tmp_path)
    assert not (tmp_path / "Other").exists()  # partial rip stays in staging


def test_rip_without_audio_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run_tail", fake_cyanrip(["Album/cover.jpg"]))
    with pytest.raises(rip.RipError, match="no audio files"):
        rip.rip("cyanrip", default_cfg(), tmp_path)


# --- tags --------------------------------------------------------------------
def test_expand_command(tmp_path):
    files = [tmp_path / "1.flac", tmp_path / "2.flac"]
    cmd = tags.expand_command(["flatpak", "run", "app", "@@", "{files}", "@@", "--dir={dir}"], tmp_path, files)
    assert cmd == ["flatpak", "run", "app", "@@", str(files[0]), str(files[1]), "@@", f"--dir={tmp_path}"]


def test_missing_tags_and_album_artist(tmp_path):
    good = make_flac(tmp_path / "01.flac", artist="IU", title="Love wins all", albumartist="IU")
    bad = make_flac(tmp_path / "02.flac", artist="IU")
    (tmp_path / "cover.jpg").write_text("x")
    files = tags.audio_files(tmp_path)
    assert files == [good, bad]
    assert tags.missing_tags(files) == [(bad, ["title"])]
    assert tags.album_artist(files) == "IU"


def test_tag_check_mentions_album_artist_setting(tmp_path, capsys):
    make_flac(tmp_path / "01.flac", title="Blueming", albumartist="IU")  # no track artist
    assert not app.check_tags(tmp_path)
    out = capsys.readouterr().out
    assert "01.flac: no artist" in out and "prefer_album_artist" in out


def test_broken_file_raises_tag_read_error(tmp_path):
    broken = tmp_path / "01.flac"
    broken.write_bytes(b"fLaC garbage")
    with pytest.raises(tags.TagReadError):
        tags.read_tags(broken)


# --- state -------------------------------------------------------------------
def test_state_round_trip_and_discovery(tmp_path):
    album_dir = tmp_path / "Album"
    album_dir.mkdir()
    album = state.AlbumState(album_dir)
    album.mark("rip")
    album.mark("tags", skipped=True)
    loaded = state.load(album_dir)
    assert loaded.done == ["rip"] and loaded.skipped == ["tags"]
    assert loaded.next_step() == "lyrics"
    assert [a.folder for a in state.unfinished_albums(tmp_path)] == [album_dir]
    loaded.mark("tags")  # done later after all
    assert state.load(album_dir).skipped == []


def test_folder_without_state_starts_after_rip(tmp_path):
    assert state.load(tmp_path).next_step() == "tags"


def test_leftover_staging(tmp_path):
    (tmp_path / f"{STAGING_PREFIX}20261011-120000").mkdir()
    (tmp_path / "Album").mkdir()
    assert [p.name for p in state.leftover_staging(tmp_path)] == [f"{STAGING_PREFIX}20261011-120000"]


# --- library / lyrics --------------------------------------------------------
def test_destination_sanitises_tag_values(tmp_path):
    dest = library.destination(tmp_path, "{albumartist}/{folder}", Path("/x/Back in Black"), "AC/DC")
    assert dest == tmp_path / "AC_DC" / "Back in Black"
    assert library.destination(tmp_path, "{folder}", Path("/x/A"), "") == tmp_path / "A"


def test_move_refuses_existing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "dest").mkdir()
    with pytest.raises(library.MoveError):
        library.move_album(src, tmp_path / "dest")
    library.move_album(src, tmp_path / "lib" / "Artist" / "Album")
    assert (tmp_path / "lib" / "Artist" / "Album").is_dir() and not src.exists()


def test_lyrembed_command(tmp_path):
    cfg = default_cfg()
    cfg["lyrembed"]["extra_args"] = ["--color", "never"]
    assert lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "auto") == [
        "lyrembed", str(tmp_path), "--auto", "--color", "never"]
    assert lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "interactive")[2:] == ["--color", "never"]


# --- proc --------------------------------------------------------------------
def test_run_waits_for_child_after_ctrl_c(monkeypatch):
    class FakePopen:
        waits = 0

        def __init__(self, cmd, cwd=None):
            pass

        def wait(self, timeout=None):
            FakePopen.waits += 1
            if FakePopen.waits == 1:
                raise KeyboardInterrupt
            return 130

        def terminate(self):
            pytest.fail("child terminated although it exited in time")

    monkeypatch.setattr(proc.subprocess, "Popen", FakePopen)
    with pytest.raises(KeyboardInterrupt):
        proc.run(["cyanrip"])
    assert FakePopen.waits == 2


def test_launch_reports_early_failure():
    launched = proc.launch([sys.executable, "-c", "import sys; sys.stderr.write('no display'); sys.exit(1)"],
                           settle_seconds=10)
    assert launched.failure() == "no display"
    ok = proc.launch([sys.executable, "-c", "pass"], settle_seconds=10)
    assert ok.failure() is None  # single-instance apps exit 0 at once


# --- whole runs --------------------------------------------------------------
@pytest.fixture
def album_dir(tmp_path):
    folder = tmp_path / "work" / "Album"
    folder.mkdir(parents=True)
    make_flac(folder / "01.flac", artist="IU", title="Blueming", albumartist="IU")
    return folder


def run_cli(tmp_path, monkeypatch, argv, *, lyrembed=None, editor=None):
    monkeypatch.setattr(tools, "find_cyanrip", lambda cfg: None)
    # A route that never prompts, so these runs skip the install offer.
    monkeypatch.setattr(install, "choose_route", lambda *a, **k: install.Route("download", "later"))
    # The fake tools only exist as names, and the cover offer has tests of its own.
    monkeypatch.setattr(tools, "start_problem", lambda cmd: None)
    monkeypatch.setattr(lyrics, "probe", lambda lyrembed: lyrics.Features())
    monkeypatch.setattr(app, "offer_cover", lambda folder: None)
    monkeypatch.setattr(tools, "find_lyrembed", lambda cfg: lyrembed)
    monkeypatch.setattr(tools, "find_editor", lambda cfg, key: editor)
    monkeypatch.setattr(tools, "available_editors", lambda cfg: [editor] if editor else [])
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"work_dir": str(tmp_path / "work"), "library_dir": str(tmp_path / "lib")}),
                        encoding="utf-8")
    return cli.main(["--config", str(cfg_path), "--color", "never"] + argv)


RELEASE_ID = "c4716917-5064-48a8-a63b-00d024f57743"


def disc_folder(folder, disc, tracks, release=RELEASE_ID, cover=b"same cover"):
    """One disc of a two-disc album as cyanrip names it."""
    folder.mkdir(parents=True)
    for n in range(1, tracks + 1):
        make_flac(folder / f"{disc}.{n:02} - T{n}.flac", album="Young Forever", albumartist="BTS",
                  artist="BTS", title=f"T{n}", discnumber=str(disc), totaldiscs="2",
                  musicbrainz_albumid=release)
    (folder / f"Young Forever CD{disc}.cue").write_text(f"cue {disc}")
    (folder / "cover.jpg").write_bytes(cover)
    return folder


def test_second_disc_joins_the_first_in_the_library(tmp_path, monkeypatch, capsys):
    library_folder = disc_folder(tmp_path / "lib" / "BTS" / "Young Forever", 1, 2)
    second = disc_folder(tmp_path / "work" / "Young Forever", 2, 2)
    (second / "Young Forever CD2.log").write_text("log 2")
    state.AlbumState(second, done=["rip", "tags", "lyrics"]).save()
    session = app.new_session(dict(default_cfg(), library_dir=str(tmp_path / "lib")), app.Options(),
                              tmp_path / "config.json")
    session.work_dir = tmp_path / "work"
    session.library_dir = tmp_path / "lib"
    album = state.load(second)
    answers = feed_input(monkeypatch, [""])  # Enter: add the disc
    assert app.finish_step(session, album) and answers == []
    names = sorted(p.name for p in library_folder.iterdir())
    assert names == ["1.01 - T1.flac", "1.02 - T2.flac", "2.01 - T1.flac", "2.02 - T2.flac",
                     "Young Forever CD1.cue", "Young Forever CD2.cue", "cover.jpg"]
    assert not second.exists()  # nothing left in the working folder, not even the state file
    assert (tmp_path / "work" / "logs" / "Young Forever CD2.log").read_text() == "log 2"
    assert "It holds disc 1 of this album; this is disc 2." in capsys.readouterr().out


def test_disc_merge_is_only_offered_when_it_is_safe(tmp_path):
    there = disc_folder(tmp_path / "lib", 1, 1)
    assert library.plan_disc_merge(disc_folder(tmp_path / "same-disc", 1, 1), there) is None
    assert library.plan_disc_merge(disc_folder(tmp_path / "other", 2, 1, release="x"), there) is None
    with pytest.raises(library.MoveError, match="cover.jpg already exists with other content"):
        library.plan_disc_merge(disc_folder(tmp_path / "new-cover", 2, 1, cover=b"other"), there)
    plan = library.plan_disc_merge(disc_folder(tmp_path / "ok", 2, 1), there)
    assert plan.discs_there == ["1"] and plan.discs_here == ["2"]
    assert [f.name for f in plan.duplicates] == ["cover.jpg"]
    # Without release IDs, album and album artist decide.
    no_id = tmp_path / "no-id"
    no_id.mkdir()
    make_flac(no_id / "2.01 - A.flac", album="Young Forever", albumartist="BTS", discnumber="2/2")
    assert library.plan_disc_merge(no_id, there).discs_here == ["2"]


def test_disc_merge_without_album_artist_compares_every_track(tmp_path):
    there, here = tmp_path / "lib", tmp_path / "work"
    there.mkdir()
    here.mkdir()
    make_flac(there / "1.01 - A.flac", album="Mix", artist="IU feat. Suga", discnumber="1")
    make_flac(there / "1.02 - B.flac", album="Mix", artist="IU", discnumber="1")
    make_flac(here / "2.01 - C.flac", album="Mix", artist="Zico", discnumber="2")
    make_flac(here / "2.02 - D.flac", album="Mix", artist="IU", discnumber="2")
    assert library.plan_disc_merge(here, there).discs_here == ["2"]  # IU is on both
    make_flac(here / "2.02 - D.flac", album="Mix", artist="Psy", discnumber="2")
    assert library.plan_disc_merge(here, there) is None  # no artist in common


def test_failed_disc_merge_moves_everything_back(tmp_path, monkeypatch):
    there = disc_folder(tmp_path / "lib", 1, 1)
    here = disc_folder(tmp_path / "work", 2, 2)
    plan = library.plan_disc_merge(here, there)
    real_move = library.shutil.move
    moves = []

    def move(source, destination):
        moves.append(source)
        if len(moves) == 2:
            raise OSError(28, "No space left on device")
        return real_move(source, destination)
    monkeypatch.setattr(library.shutil, "move", move)
    with pytest.raises(library.MoveError, match="No space left on device.*stays where it was"):
        library.merge_disc(plan, there)
    assert sorted(p.name for p in there.iterdir()) == ["1.01 - T1.flac", "Young Forever CD1.cue", "cover.jpg"]
    assert len(list(here.glob("*.flac"))) == 2 and (here / "cover.jpg").exists()


def test_existing_folder_to_library(tmp_path, monkeypatch, album_dir):
    # Tags: continue; Finish: move (default); no other run (default).
    answers = feed_input(monkeypatch, ["c", "", ""])
    code = run_cli(tmp_path, monkeypatch, [str(album_dir), "--lyrics", "skip"])
    assert code == 0 and answers == []
    moved = tmp_path / "lib" / "IU" / "Album"
    assert (moved / "01.flac").exists()
    assert not (moved / STATE_FILE_NAME).exists()


def test_another_run_after_the_summary(tmp_path, monkeypatch, album_dir, capsys):
    # First run: the folder; then another run, which offers nothing and asks
    # for an album folder (cyanrip is missing); empty goes back and ends.
    answers = feed_input(monkeypatch, ["c", "k", "y"])
    monkeypatch.setattr(app.ui, "ask_path", lambda prompt, prefill="": "")
    assert run_cli(tmp_path, monkeypatch, [str(album_dir), "--lyrics", "skip"]) == 0
    assert answers == []
    out = capsys.readouterr().out
    assert out.count("Summary") == 1 and "only an album ripped earlier" in out


def test_auto_lyrics_failure_falls_back_to_interactive(tmp_path, monkeypatch, album_dir):
    modes = []

    def fake_run(lyrembed, cfg, folder, mode, romanize=None, return_url=''):
        modes.append(mode)
        return lyrics.EXIT_BATCH_FAILURES if mode == "auto" else lyrics.EXIT_OK
    monkeypatch.setattr(app.lyrics, "run_lyrembed", fake_run)
    # Tags: continue; lyrics: auto; then: interactive; finish: keep.
    feed_input(monkeypatch, ["c", "a", "i", "k", ""])
    code = run_cli(tmp_path, monkeypatch, [str(album_dir)], lyrembed="lyrembed")
    assert code == 0
    assert modes == ["auto", "interactive"]
    assert album_dir.exists() and not (album_dir / STATE_FILE_NAME).exists()


def test_ctrl_c_leaves_resumable_state(tmp_path, monkeypatch, album_dir, capsys):
    def interrupted(lyrembed, cfg, folder, mode, romanize=None, return_url=''):
        raise KeyboardInterrupt
    monkeypatch.setattr(app.lyrics, "run_lyrembed", interrupted)
    feed_input(monkeypatch, ["c"])
    code = run_cli(tmp_path, monkeypatch, [str(album_dir), "--lyrics", "auto"], lyrembed="lyrembed")
    assert code == 130
    assert "continue with Album" in capsys.readouterr().out
    saved = state.load(album_dir)
    assert saved.next_step() == "lyrics"

    # Next run without a folder offers the unfinished album first.
    monkeypatch.setattr(app.lyrics, "run_lyrembed", lambda *a: lyrics.EXIT_OK)
    feed_input(monkeypatch, ["1", "", "k", ""])  # resume; lyrics: continue; keep; no other run
    assert run_cli(tmp_path, monkeypatch, ["--lyrics", "auto"], lyrembed="lyrembed") == 0
    assert not (album_dir / STATE_FILE_NAME).exists()


def test_editor_is_opened_and_tags_rechecked(tmp_path, monkeypatch, album_dir):
    make_flac(album_dir / "02.flac", artist="IU")  # no title
    editor = tools.Editor("eartag", "Ear Tag", ["eartag", "{files}"])
    launched_with, closed = [], []

    def fake_launch(cmd, settle_seconds=2.0):
        launched_with.append(cmd)
        return SimpleNamespace(failure=lambda: None, close=lambda: closed.append(True))
    monkeypatch.setattr(app.tags.proc, "launch", fake_launch)
    # Tags: open (default), Enter when done, continue, refuse "continue anyway",
    # continue again and accept; finish: keep.
    feed_input(monkeypatch, ["", "", "c", "n", "c", "y", "k", ""])
    code = run_cli(tmp_path, monkeypatch, [str(album_dir), "--lyrics", "skip"], editor=editor)
    assert code == 0
    assert launched_with == [["eartag", str(album_dir / "01.flac"), str(album_dir / "02.flac")]]
    assert closed == [True]                               # its error output is not left open


LS_REMOTE = (
    "b40c1f006a000a8bbd2d7ccc438a17006b9b71a0\trefs/heads/master\n"
    "11fe09435e289f6522ea9e7f8829308472b15704\trefs/tags/v0.9.3\n"
    "171b01effbab83e90919787e3912575b1b650fd1\trefs/tags/v0.9.3.1\n"
    "063d848000000000000000000000000000000000\trefs/tags/v0.10.0-rc1\n"
    "c79e5461ad0c3e339b985d08786f7d096e4b0f1a\trefs/tags/v0.9.2\n"
)


# --- installing cyanrip ------------------------------------------------------
@pytest.mark.parametrize("release, which, kind, command", [
    ({"ID": "fedora"}, {}, "source", None),
    ({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}, {}, "package", ["apt", "install", "cyanrip"]),
    ({"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"}, {}, "package", ["zypper", "install", "cyanrip"]),
    ({"ID": "opensuse-leap", "ID_LIKE": "suse opensuse"}, {}, "source", None),
    ({"ID": "endeavouros", "ID_LIKE": "arch"}, {"paru"}, "package", ["paru", "-S", "cyanrip"]),
    ({"ID": "arch"}, set(), "source", None),
])
def test_install_route(monkeypatch, release, which, kind, command):
    monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    route = install.choose_route(release, platform="linux", which=lambda name: name if name in which else None)
    assert route.kind == kind
    if command:
        assert route.command[-len(command):] == command
        # sudo only for system package managers, never for AUR helpers.
        assert (route.command[0] == "sudo") == (command[0] in ("apt", "zypper"))


def test_install_route_windows():
    assert install.choose_route({}, platform="win32").kind == "download"


def test_missing_libs_asks_pkg_config(monkeypatch):
    asked = []

    def capture(cmd, timeout=10):
        asked.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 1 if "musicbrainz" in cmd[-1] else 0, "", "")
    monkeypatch.setattr(install.proc, "capture", capture)
    assert install.missing_libs(install.REQUIRED_LIBS) == ["libmusicbrainz5"]
    assert "libavcodec >= 59.24.100" in asked
    monkeypatch.setattr(install.proc, "capture", lambda cmd, timeout=10: None)  # no pkg-config
    assert len(install.missing_libs(install.REQUIRED_LIBS)) == len(install.REQUIRED_LIBS)


@pytest.mark.parametrize("rpmfusion, ffmpeg_package", [(True, "ffmpeg-devel"), (False, "pkgconfig(libavcodec)")])
def test_fedora_packages(monkeypatch, rpmfusion, ffmpeg_package):
    monkeypatch.setattr(install.proc, "capture",
                        lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0 if rpmfusion else 1, "", ""))
    packages = install.fedora_packages(["meson", "ninja", "libavcodec", "libavutil", "libmusicbrainz5", "libcurl"])
    assert packages[:2] == ["meson", "ninja-build"]
    assert ffmpeg_package in packages and "libmusicbrainz5-devel" in packages and "libcurl-devel" in packages
    if rpmfusion:
        assert packages.count("ffmpeg-devel") == 1


def test_check_source_dir(tmp_path, monkeypatch):
    assert install.check_source_dir(tmp_path / "new") == "clone"
    assert install.check_source_dir(tmp_path) == "clone"  # empty
    (tmp_path / "file.txt").write_text("x")
    assert "not empty" in install.check_source_dir(tmp_path)
    (tmp_path / ".git").mkdir()
    remote = "https://github.com/cyanreg/cyanrip.git\n"
    monkeypatch.setattr(install.proc, "capture", lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0, remote, ""))
    assert install.check_source_dir(tmp_path) == "update"
    monkeypatch.setattr(install.proc, "capture",
                        lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0, "git@example.org:me/x\n", ""))
    assert "other than cyanrip" in install.check_source_dir(tmp_path)


def test_build_commands(tmp_path):
    source, build = tmp_path / "cyanrip", tmp_path / "out"
    git = ["git", "-C", str(source)]
    release = install.build_commands(source, "clone", build, tag="v0.9.3.1")
    assert [c for c, _ in release[:2]] == [["git", "clone", install.REPO_URL, str(source)],
                                           git + ["checkout", "--detach", "v0.9.3.1"]]
    setup = release[2][0]
    assert setup[:3] == ["meson", "setup", str(build)] and f"--prefix={Path.home() / '.local'}" in setup
    assert "--reconfigure" not in setup

    master_clone = install.build_commands(source, "clone", build)
    assert master_clone[1][0][:2] == ["meson", "setup"]  # a fresh clone is on master already

    (build / "meson-private").mkdir(parents=True)
    update = install.build_commands(source, "update", build)
    assert [c for c, _ in update[:3]] == [git + ["fetch", "--tags", "origin"], git + ["checkout", "master"],
                                          git + ["merge", "--ff-only", "origin/master"]]
    assert "--reconfigure" in update[3][0]
    assert [c[0] for c in update[4:]] == [["meson", "compile", "-C", str(build)],
                                          ["meson", "install", "-C", str(build)]]
    assert all(cwd == source for _, cwd in update[3:])


def test_check_build_dir(tmp_path):
    source = tmp_path / "src"
    assert install.check_build_dir(source / "build", source) is None
    assert "differ" in install.check_build_dir(source, source)
    other = tmp_path / "other"
    (other / "x").mkdir(parents=True)
    assert "not a meson build folder" in install.check_build_dir(other, source)
    (other / "meson-private").mkdir()
    assert install.check_build_dir(other, source) is None


def test_dirs_are_prefilled_with_defaults_and_remembered_folders(monkeypatch):
    asked = []

    def accept(prompt, prefill=""):
        asked.append(prefill)
        return prefill  # the user presses Enter
    monkeypatch.setattr(install.ui, "ask_path", accept)
    monkeypatch.setattr(install, "check_source_dir", lambda path: "clone")
    monkeypatch.setattr(install, "check_build_dir", lambda build, source: None)

    source, state, build = install.ask_dirs(default_cfg())
    assert asked == ["~/.local/src/cyanrip", "~/.local/src/cyanrip/build"]
    assert source == Path.home() / ".local/src/cyanrip" and build == source / "build"

    cfg = default_cfg()
    cfg.update(cyanrip_source_dir="~/Software/Git-Repos/cyanrip", cyanrip_build_dir="/tmp/cyanrip-build")
    asked.clear()
    install.ask_dirs(cfg)
    assert asked == ["~/Software/Git-Repos/cyanrip", "/tmp/cyanrip-build"]


def test_build_prefill_follows_a_changed_source(tmp_path):
    cfg = default_cfg()
    cfg.update(cyanrip_source_dir="/old/cyanrip", cyanrip_build_dir="/old/out")
    assert install._build_prefill(cfg, Path("/new/cyanrip"), "/new/cyanrip") == "/new/cyanrip/build"


def test_cleared_line_cancels(monkeypatch):
    monkeypatch.setattr(install.ui, "ask_path", lambda prompt, prefill="": "")
    assert install.ask_dirs(default_cfg()) is None


def test_ask_path_without_readline_accepts_prefill(monkeypatch):
    monkeypatch.setattr(ui, "_readline", None)
    feed_input(monkeypatch, [""])
    assert ui.ask_path("Folder", "~/x") == "~/x"


def test_tilde():
    assert ui.tilde(Path.home() / "Musik") == "~/Musik"
    assert ui.tilde("/tmp/x") == "/tmp/x"


def test_source_build_on_fedora(tmp_path, monkeypatch):
    """Whole flow with every command mocked: deps via dnf, clone, build, remember."""
    state = {"installed": False}
    ran = []

    def capture(cmd, timeout=10):
        if cmd[0] == "pkg-config":
            ok = state["installed"] or not any(n in cmd[-1] for n in ("musicbrainz", "curl", "qrencode", "chromaprint"))
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "")
        if cmd[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(cmd, 0, LS_REMOTE, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")  # rpm -q ffmpeg-libs: installed

    def run(cmd, cwd=None):
        ran.append((cmd, cwd))
        if "dnf" in cmd:
            state["installed"] = True
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[-1]).mkdir(parents=True)
        return 0

    monkeypatch.setattr(install.proc, "capture", capture)
    monkeypatch.setattr(install.proc, "run", run)
    monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(install, "missing_tools", lambda which=None: [] if state["installed"] else ["meson", "ninja"])
    monkeypatch.setattr(install.tools, "find_cyanrip", lambda cfg: "/home/u/.local/bin/cyanrip")
    source = tmp_path / "src" / "cyanrip"
    build = tmp_path / "build" / "cyanrip"
    config_path = tmp_path / "config.json"
    config_path.write_text('{"work_dir": "~/Rips"}', encoding="utf-8")
    # optional libs: no; run dnf: yes; source folder; build folder; run build: yes
    feed_input(monkeypatch, ["n", "", str(source), str(build), ""])

    cfg = default_cfg()
    found = install.install_cyanrip(cfg, config_path, os_release={"ID": "fedora"}, platform="linux")
    assert found == "/home/u/.local/bin/cyanrip"
    assert ran[0][0] == ["sudo", "dnf", "install", "meson", "ninja-build", "libmusicbrainz5-devel", "libcurl-devel"]
    assert [c[0][:2] for c in ran[1:]] == [["git", "clone"], ["git", "-C"], ["meson", "setup"],
                                           ["meson", "compile"], ["meson", "install"]]
    assert ran[2][0][-2:] == ["--detach", "v0.9.3.1"]  # channel "release": the newest tag
    assert all(cwd == source for _, cwd in ran[3:])
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved == {"work_dir": "~/Rips", "cyanrip_source_dir": str(source), "cyanrip_build_dir": str(build)}
    assert ran[3][0][2] == str(build)  # meson setup <build>


def test_source_build_stops_at_failed_command(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "install_dependencies", lambda os_release: True)
    monkeypatch.setattr(install, "remote_info", lambda max_age=0: install.Remote("v0.9.3.1", "b40c1f0aa"))
    monkeypatch.setattr(install.proc, "run", lambda cmd, cwd=None: 1 if cmd[1] == "compile" else 0)
    monkeypatch.setattr(install.proc, "capture", lambda cmd, timeout=10: None)
    feed_input(monkeypatch, [str(tmp_path / "cyanrip"), str(tmp_path / "cyanrip" / "build"), ""])
    config_path = tmp_path / "config.json"
    assert install.build_from_source(default_cfg(), config_path, {"ID": "fedora"}) is None
    assert not config_path.exists()  # nothing remembered for a failed build


def test_remember_keeps_other_settings_and_refuses_broken_files(tmp_path):
    path = tmp_path / "sub" / "config.json"
    config.remember(path, {"cyanrip_source_dir": "~/src/cyanrip"})
    config.remember(path, {"cyanrip_build_dir": "~/src/cyanrip/build"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"cyanrip_source_dir": "~/src/cyanrip",
                                                            "cyanrip_build_dir": "~/src/cyanrip/build"}
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        config.remember(path, {"cyanrip_build_dir": "x"})
    assert path.read_text(encoding="utf-8") == "{broken"


def test_step_0_offers_install_when_cyanrip_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "find_cyanrip", lambda cfg: None)
    monkeypatch.setattr(tools, "find_lyrembed", lambda cfg: None)
    monkeypatch.setattr(tools, "find_editor", lambda cfg, key: None)
    monkeypatch.setattr(tools, "available_editors", lambda cfg: [])
    monkeypatch.setattr(install, "choose_route", lambda *a, **k: install.Route("source", "build"))
    calls = []
    monkeypatch.setattr(install, "install_cyanrip", lambda cfg, path: calls.append(path) or "/x/cyanrip")
    feed_input(monkeypatch, [""])  # Install cyanrip now? -> yes
    session = app.new_session(default_cfg(), app.Options(), tmp_path / "config.json")
    app.check_tools(session)
    assert calls == [tmp_path / "config.json"] and session.cyanrip == "/x/cyanrip"


# --- drive offset ------------------------------------------------------------
def offset_session(tmp_path, offset=None):
    cfg = default_cfg()
    cfg["cyanrip"]["offset"] = offset
    session = app.new_session(cfg, app.Options(), tmp_path / "config.json")
    session.cyanrip = "cyanrip"
    return session


def test_offset_already_set_asks_nothing(tmp_path, monkeypatch):
    feed_input(monkeypatch, [])
    assert app.ensure_offset(offset_session(tmp_path, offset=6), "/dev/sr0")


def test_entered_offset_is_saved_next_to_other_settings(tmp_path, monkeypatch):
    session = offset_session(tmp_path)
    session.config_path.write_text('{"cyanrip": {"eject": false}, "work_dir": "~/Rips"}', encoding="utf-8")
    feed_input(monkeypatch, ["e", "+6"])
    assert app.ensure_offset(session, "/dev/sr0")
    assert session.cfg["cyanrip"]["offset"] == 6
    saved = json.loads(session.config_path.read_text(encoding="utf-8"))
    assert saved == {"cyanrip": {"eject": False, "offset": 6}, "work_dir": "~/Rips"}
    assert rip.cyanrip_args(session.cfg)[:2] == ["-s", "6"]


def test_offset_zero_is_for_this_rip_only(tmp_path, monkeypatch):
    session = offset_session(tmp_path)
    feed_input(monkeypatch, ["0"])
    assert app.ensure_offset(session, "")
    assert session.cfg["cyanrip"]["offset"] == 0
    assert not session.config_path.exists()
    # The next disc (another run) asks again instead of ripping with 0 silently.
    session.forget_one_time_offset()
    assert session.cfg["cyanrip"]["offset"] is None
    feed_input(monkeypatch, ["b"])
    assert not app.ensure_offset(session, "")


def test_failed_measurement_offers_the_choice_again(tmp_path, monkeypatch):
    commands = []

    def run_tail(cmd, cwd=None, lines=3, keep_bytes=8192):
        commands.append(cmd)
        return 1, ["No track had AccuRip entry, cannot find offset!"]
    monkeypatch.setattr(app.proc, "run_tail", run_tail)
    feed_input(monkeypatch, ["f", "b"])
    assert not app.ensure_offset(offset_session(tmp_path), "/dev/sr0")
    assert commands == [["cyanrip", "-f", "-d", "/dev/sr0"]]


def test_measured_offset_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(app.proc, "run_tail", lambda cmd, cwd=None, lines=3: (0, []))
    session = offset_session(tmp_path)
    feed_input(monkeypatch, ["f", "x", "f", "-12"])  # a typo, then measure again
    assert app.ensure_offset(session, "")
    assert json.loads(session.config_path.read_text(encoding="utf-8")) == {"cyanrip": {"offset": -12}}


def test_rip_failing_before_any_file_removes_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run_tail", lambda cmd, cwd=None: (1, []))
    with pytest.raises(rip.RipError, match="before writing anything"):
        rip.rip("cyanrip", default_cfg(), tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- showing why a tool failed -----------------------------------------------
def test_last_lines_strips_progress_and_colours():
    out = (b"Opening drive...\r\n\x1b[33mOffset is unset! To continue with an offset of 0, "
           b"run with -s 0!\x1b[0m\r\n\r\n")
    assert proc.last_lines(out, 1) == ["Offset is unset! To continue with an offset of 0, run with -s 0!"]
    assert proc.last_lines(b"Ripping 10%\rRipping 55%\rRipping 99%", 1) == ["Ripping 99%"]


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals are POSIX-only")
def test_run_tail_passes_output_through_and_keeps_the_end(monkeypatch, capsys):
    monkeypatch.setattr(proc, "_use_pty", lambda: True)
    child = ("import sys; print('Opening drive...'); "
             "print('Offset is unset!', file=sys.stderr); sys.exit(1)")
    code, tail = proc.run_tail([sys.executable, "-c", child])
    assert code == 1
    assert tail[-1] == "Offset is unset!" and "Opening drive..." in tail
    assert "Offset is unset!" in capsys.readouterr().out  # the user saw it live too


def test_run_tail_without_a_terminal_still_keeps_the_end(monkeypatch, capsys):
    monkeypatch.setattr(proc, "_use_pty", lambda: False)
    child = ("import sys; print('Multiple releases found'); "
             "print('    1 (ID: x): Album', file=sys.stderr); sys.exit(1)")
    code, tail = proc.run_tail([sys.executable, "-c", child], lines=10)
    # A pipe makes the tool buffer stdout but not stderr, so the order may change.
    assert code == 1 and sorted(tail) == ["1 (ID: x): Album", "Multiple releases found"]
    assert "Multiple releases found" in capsys.readouterr().out
    assert proc.run_tail(["/nonexistent/tool"])[0] == proc.CANNOT_START


@pytest.mark.skipif(sys.platform == "win32", reason="signals to a child differ on Windows")
def test_run_tail_keeps_reading_while_the_tool_cleans_up(monkeypatch, capsys):
    # After Ctrl+C the tool writes more than a terminal buffer holds; it
    # must still be able to finish within the grace period.
    monkeypatch.setattr(proc, "_use_pty", lambda: True)
    child = ("import signal, sys, time\n"
             "def bye(*a):\n"
             "    sys.stdout.write('x' * 200000 + '\\ncleaned up\\n'); sys.stdout.flush(); sys.exit(3)\n"
             "signal.signal(signal.SIGINT, bye)\n"
             "print('ready', flush=True)\n"
             "time.sleep(30)\n")
    real_copy = []
    started = subprocess.Popen

    def popen(*args, **kwargs):
        child_proc = started(*args, **kwargs)
        real_copy.append(child_proc)
        return child_proc
    monkeypatch.setattr(proc.subprocess, "Popen", popen)
    monkeypatch.setattr(proc, "INTERRUPT_GRACE_SECONDS", 10)
    real_write = sys.stdout.buffer.write
    interrupted = []

    def write(data):
        # Ctrl+C reaches the tool and cdimport together, as from the terminal.
        if b"ready" in data and not interrupted:
            interrupted.append(1)
            real_copy[0].send_signal(2)
            real_write(data)
            raise KeyboardInterrupt
        return real_write(data)
    monkeypatch.setattr(sys.stdout.buffer, "write", write)
    import time
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        proc.run_tail([sys.executable, "-c", child])
    assert real_copy[0].returncode == 3  # finished on its own, not terminated
    assert time.monotonic() - began < 10


def test_rip_error_repeats_cyanrips_reason(tmp_path, monkeypatch):
    reason = "Offset is unset! To continue with an offset of 0, run with -s 0!"
    monkeypatch.setattr(rip.proc, "run_tail", lambda cmd, cwd=None: (1, ["Opening drive...", reason]))
    with pytest.raises(rip.RipError) as caught:
        rip.rip("cyanrip", default_cfg(), tmp_path)
    assert f"cyanrip: {reason}" in str(caught.value)


def test_failed_rip_offers_to_try_again(tmp_path, monkeypatch):
    session = offset_session(tmp_path, offset=6)
    session.work_dir = tmp_path / "work"
    monkeypatch.setattr(app.rip, "preview", lambda *a, **k: rip.Preview(0, []))
    attempts = []

    def fake_rip(cyanrip, cfg, work_dir, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise rip.RipError("cyanrip failed (exit code 1):\n  cyanrip: drive not ready")
        folder = work_dir / "Album"
        folder.mkdir(parents=True)
        return folder, None
    monkeypatch.setattr(app.rip, "rip", fake_rip)
    feed_input(monkeypatch, ["r", "r", "r"])  # rip, back to the preview, rip again
    album = app.preview_and_rip(session, "/dev/sr0")
    assert album is not None and album.folder == session.work_dir / "Album"
    assert len(attempts) == 2 and not session.failed


# --- discs MusicBrainz does not know ------------------------------------------
# cyanrip 0.9.4-rc3 (b40c1f0), -I for a disc without a MusicBrainz entry.
UNKNOWN_DISC = [
    "Unable to find release info for this CD, and metadata hasn't been manually added!",
    "Please help improve the MusicBrainz DB by submitting the disc info via the following URL:",
    "https://musicbrainz.org/cdtoc/attach?toc=1+6+150000+150+20000+40000+60000+80000+100000&tracks=6&id=abc-",
    "\u2588\u2588\u2580\u2580 QR code",
    "To continue add metadata via -a or -t, or ignore via -N!",
]


def test_typed_tags_become_escaped_a_and_t_options():
    tags = rip.ManualTags("Killin' It: Vol=1", "P1Harmony", "2024", {1: "A:B", 2: ""})
    assert rip.tag_args(tags) == ["-a", "album=Killin\\' It\\: Vol\\=1:album_artist=P1Harmony:date=2024",
                                  "-t", "1=title=A\\:B"]
    assert rip.tag_args(rip.ManualTags(album="X")) == ["-a", "album=X"]
    args = rip.cyanrip_args(default_cfg(), tags=tags, info_only=True)
    assert args[args.index("-a") + 1].startswith("album=") and "-I" in args


def test_preview_finds_the_submission_link_and_track_numbers(monkeypatch):
    calls = []
    monkeypatch.setattr(rip.proc, "run_tail",
                        lambda cmd, cwd=None, lines=3, keep_bytes=8192: calls.append(cmd) or (1, UNKNOWN_DISC))
    shown = rip.preview("cyanrip", default_cfg())
    assert shown.needs_tags and shown.disc_unknown and shown.tracks == (1, 6)
    assert shown.submission_url == UNKNOWN_DISC[2]
    # A release picked by ID: tags are there, the disc ID still is not.
    forced = rip.parse_preview(0, ["The disc isn't on MusicBrainz yet, consider attaching it to the release "
                                   "via the following URL.", "MusicBrainz URL:", UNKNOWN_DISC[2]])
    assert forced.disc_unknown and not forced.needs_tags
    known = rip.parse_preview(0, ["MusicBrainz URL:", UNKNOWN_DISC[2], "Album: X"])
    assert not known.disc_unknown and not known.needs_tags


def test_parse_release_answer():
    mbid = "5c580fb1-7c31-4785-afde-68098dc5caa2"
    assert musicbrainz.parse_release("2") == "2"
    assert musicbrainz.parse_release(f"https://musicbrainz.org/release/{mbid.upper()}/discids") == mbid
    assert musicbrainz.parse_release(mbid) == mbid
    assert musicbrainz.parse_release("Killin It") == ""


def test_search_releases_labels_and_track_counts(monkeypatch):
    asked = []

    def http_get(url, timeout=20):
        asked.append(url)
        if "Deluxe" in url:
            return b'{"releases": []}'
        return json.dumps({"releases": [
            {"id": "r1", "title": "Album", "date": "2024-01-02", "country": "KR", "disambiguation": "CD ver.",
             "artist-credit": [{"name": "A", "joinphrase": " & "}, {"name": "B"}],
             "media": [{"format": "CD", "track-count": 6}, {"format": "CD", "track-count": 1}]}]}).encode()
    monkeypatch.setattr(cover, "http_get", http_get)
    found = musicbrainz.search_releases("Album (Deluxe)", "A")
    assert len(asked) == 2  # again without the edition note
    assert found[0].label == "Album - A & B (2024, KR, CD ver.) · CD 6 tracks + CD 1 track"
    assert found[0].track_counts == [6, 1] and found[0].page == "https://musicbrainz.org/release/r1"


def unknown_disc_session(tmp_path, monkeypatch):
    """A session whose disc MusicBrainz does not know until a release or tags are given."""
    session = offset_session(tmp_path, offset=6)
    session.work_dir = tmp_path / "work"
    previews = []

    def preview(cyanrip, cfg, **kwargs):
        previews.append(kwargs)
        if kwargs["release"] or kwargs["tags"] or kwargs["no_musicbrainz"]:
            return rip.parse_preview(0, ["The disc isn't on MusicBrainz yet, consider attaching it.",
                                         UNKNOWN_DISC[2]])
        return rip.parse_preview(1, UNKNOWN_DISC)
    ripped = []

    def fake_rip(cyanrip, cfg, work_dir, **kwargs):
        ripped.append(kwargs)
        folder = work_dir / "Album"
        folder.mkdir(parents=True)
        return folder, None
    monkeypatch.setattr(app.rip, "preview", preview)
    monkeypatch.setattr(app.rip, "rip", fake_rip)
    return session, previews, ripped


def test_unknown_disc_search_picks_the_release(tmp_path, monkeypatch, capsys):
    session, previews, ripped = unknown_disc_session(tmp_path, monkeypatch)
    monkeypatch.setattr(musicbrainz, "search_releases", lambda album, artist: [
        musicbrainz.Release("r5", "Other - X · CD 10 tracks", [10]),
        musicbrainz.Release("r6", "Album - X · CD 6 tracks", [6])])
    # No "r" while cyanrip needs tags; Enter searches. Album, artist, the 2nd hit, then rip.
    answers = feed_input(monkeypatch, ["r", "", "Album", "X", "2", "r"])
    album = app.preview_and_rip(session, "/dev/sr0")
    assert answers == [] and album is not None
    assert ripped[0]["release"] == "r6" and ripped[0]["tags"] is None
    out = capsys.readouterr().out
    assert "Please type one of" in out  # "r" was not offered
    assert "same track count as the disc\n      https://musicbrainz.org/release/r6" in out
    assert UNKNOWN_DISC[2] in out and "attach the disc ID" in out


def test_unknown_disc_with_typed_tags(tmp_path, monkeypatch):
    session, previews, ripped = unknown_disc_session(tmp_path, monkeypatch)
    # Tags: album, artist, no date, six titles (the third left empty); then rip.
    answers = feed_input(monkeypatch, ["t", "Album", "X", "", "One", "Two", "", "Four", "Five", "Six", "r"])
    assert app.preview_and_rip(session, "/dev/sr0") is not None and answers == []
    tags = ripped[0]["tags"]
    assert (tags.album, tags.artist, tags.date) == ("Album", "X", "")
    assert tags.titles == {1: "One", 2: "Two", 4: "Four", 5: "Five", 6: "Six"}
    assert previews[-1]["tags"] is tags


def test_release_by_link_and_bad_answers(tmp_path, monkeypatch, capsys):
    session, previews, ripped = unknown_disc_session(tmp_path, monkeypatch)
    link = "https://musicbrainz.org/release/5c580fb1-7c31-4785-afde-68098dc5caa2"
    feed_input(monkeypatch, ["m", "nonsense", "m", link, "r"])
    app.preview_and_rip(session, "/dev/sr0")
    assert ripped[0]["release"] == "5c580fb1-7c31-4785-afde-68098dc5caa2"
    assert "neither a list number" in capsys.readouterr().out


# cyanrip 0.9.4-rc3 (b40c1f0) -I when several releases have the disc ID
# (src/musicbrainz.c; last_lines strips the four-space indent).
SEVERAL_RELEASES = [
    "Multiple releases found in database for DiscID abc-:",
    "    1 (ID: 2CABF241-341b-4b40-ad84-c77c719af676): Killin' It (XW) (2024-01-08)",
    "    2 (ID: 5c580fb1-7c31-4785-afde-68098dc5caa2): \ub54c\uae54 (Killin\u2019 It) (\uc0c9\uae54 (Superb) ver.) (KR) (2 CDs) (2024-01-08)",
    "Please specify which release to use by adding the -R argument with an index or ID.",
]


def test_several_releases_are_offered_in_the_menu(tmp_path, monkeypatch, capsys):
    shown = rip.parse_preview(1, [line.strip() for line in SEVERAL_RELEASES])
    assert shown.releases == [("2cabf241-341b-4b40-ad84-c77c719af676", "Killin' It (XW) (2024-01-08)"),
                              ("5c580fb1-7c31-4785-afde-68098dc5caa2",
                               "\ub54c\uae54 (Killin\u2019 It) (\uc0c9\uae54 (Superb) ver.) (KR) (2 CDs) (2024-01-08)")]
    assert not shown.needs_tags
    assert rip.parse_preview(0, ["1 (ID: x): not a list"]).releases == []

    session = offset_session(tmp_path, offset=6)
    session.work_dir = tmp_path / "work"
    previews = []

    def preview(cyanrip, cfg, **kwargs):
        previews.append(kwargs["release"])
        return rip.Preview(0, []) if kwargs["release"] else shown
    ripped = []

    def fake_rip(cyanrip, cfg, work_dir, **kwargs):
        ripped.append(kwargs["release"])
        (work_dir / "Album").mkdir(parents=True)
        return work_dir / "Album", None
    monkeypatch.setattr(app.rip, "preview", preview)
    monkeypatch.setattr(app.rip, "rip", fake_rip)
    answers = feed_input(monkeypatch, ["r", "2", "r"])  # no "r" before a pick; the 2nd release; rip
    assert app.preview_and_rip(session, "/dev/sr0") is not None and answers == []
    assert previews == ["", "5c580fb1-7c31-4785-afde-68098dc5caa2"]
    assert ripped == ["5c580fb1-7c31-4785-afde-68098dc5caa2"]
    out = capsys.readouterr().out
    assert "Please type one of: 1, 2, s" in out
    assert "https://musicbrainz.org/release/5c580fb1-7c31-4785-afde-68098dc5caa2" in out


# --- fast and secure rips -----------------------------------------------------
def _track_section(number, verdict, ok=True):
    """One track's part of a cyanrip log (format of master b40c1f0)."""
    return (f"Track {number} ripped and encoded {'successfully!' if ok else 'with errors.'}\n"
            "Summary:\n\n  Properties:\n    Duration:    03:07.40\n\n"
            "  EAC CRC32:     85175512\n"
            "  Accurip:       disc found in database (max confidence: 1)\n"
            f"    Accurip v1:  B04C1ADE{verdict}\n"
            "    Accurip v2:  526CDDBE\n\n  Metadata:\n    title:   X\n\n")


ACCURATE = " (accurately ripped, confidence 1)"
NOT_FOUND = " (not found, either a new pressing, or bad rip)"


def fast_log(*tracks):
    """A rip log: tracks as (number, verdict, ok)."""
    return ("cyanrip 0.9.4-rc3 (b40c1f0)\nAccurateRip:    found\n\nTracks:\n"
            + "".join(_track_section(*t) for t in tracks)
            + "Tracks ripped accurately: 1/3\n\nRipping errors: 0\n")


def test_rip_mode_options_and_accuraterip_results():
    cfg = default_cfg()
    args = rip.cyanrip_args(cfg, paranoia="none", eject=False)
    assert args[args.index("-P") + 1] == "none" and "-Q" not in args
    args = rip.cyanrip_args(cfg, paranoia="max", tracks=[3, 5])
    assert args[args.index("-l") + 1] == "3,5" and "-Q" in args
    assert rip.parse_preview(0, ["AccurateRip ID: 1-2-3", "AccurateRip:    found"]).accuraterip == "found"
    assert rip.parse_preview(0, ["AccurateRip:    not found"]).accuraterip == "not found"
    assert rip.parse_preview(0, []).accuraterip == ""
    log = fast_log((1, ACCURATE, True), (2, NOT_FOUND, True), (3, ACCURATE, False))
    assert rip.accuraterip_results(log) == {1: True, 2: False, 3: False}
    assert config.validate(dict(cfg, cyanrip=dict(cfg["cyanrip"], mode="quick")))


def test_rerip_replaces_tracks_and_keeps_both_logs(tmp_path, monkeypatch):
    work = tmp_path / "work"
    album = work / "Album"
    album.mkdir(parents=True)
    for name, text in (("1 - A.flac", "fast1"), ("2 - B.flac", "fast2"), ("Album.cue", "full cue"),
                       ("Album.log", "first log")):
        (album / name).write_text(text)
    commands = []

    def cyanrip(cmd, cwd=None, lines=3, keep_bytes=8192):
        commands.append(cmd)
        out = Path(cwd) / "Album"
        out.mkdir()
        (out / "2 - B.flac").write_text("secure2")
        (out / "Album.cue").write_text("partial cue")
        (out / "Album.log").write_text(fast_log((2, ACCURATE, True)))
        return 0, []
    monkeypatch.setattr(rip.proc, "run_tail", cyanrip)
    replaced, log, leftover = rip.rerip("cyanrip", default_cfg(), album, work, [2], eject=True)
    assert leftover is None
    assert replaced == [album / "2 - B.flac"] and (album / "2 - B.flac").read_text() == "secure2"
    assert (album / "1 - A.flac").read_text() == "fast1" and (album / "Album.cue").read_text() == "full cue"
    assert log == album / "Album (secure re-rip).log" and (album / "Album.log").read_text() == "first log"
    cmd = commands[0]
    assert cmd[cmd.index("-P") + 1] == "max" and cmd[cmd.index("-l") + 1] == "2" and "-Q" in cmd
    assert not list(work.glob(STAGING_PREFIX + "*"))  # nothing left behind
    rip.rerip("cyanrip", default_cfg(), album, work, [2])
    assert (album / "Album (secure re-rip) (2).log").exists()  # a second re-rip keeps the first log


def test_rerip_reports_what_does_not_belong(tmp_path, monkeypatch):
    work = tmp_path / "work"
    album = work / "Album"
    album.mkdir(parents=True)
    (album / "2 - B.flac").write_text("fast2")

    def cyanrip(cmd, cwd=None, lines=3, keep_bytes=8192):
        out = Path(cwd) / "Album"
        out.mkdir()
        (out / "2 - B.flac").write_text("secure2")
        (out / "2 - Other name.flac").write_text("?")  # not in the album: stays out of it
        return 0, []
    monkeypatch.setattr(rip.proc, "run_tail", cyanrip)
    replaced, log, leftover = rip.rerip("cyanrip", default_cfg(), album, work, [2])
    assert replaced == [album / "2 - B.flac"] and (album / "2 - B.flac").read_text() == "secure2"
    assert leftover and (leftover / "Album" / "2 - Other name.flac").exists()
    assert not (album / "2 - Other name.flac").exists()


def fast_rip_session(tmp_path, monkeypatch, log_text, accuraterip="found", relog=None):
    """A session whose disc rips into work/Album with the given log; returns
    the session and what was called."""
    session = offset_session(tmp_path, offset=6)
    session.work_dir = tmp_path / "work"
    calls = {"rip": [], "rerip": [], "eject": []}
    monkeypatch.setattr(app.rip, "preview", lambda *a, **k: rip.Preview(0, [], accuraterip=accuraterip))

    def fake_rip(cyanrip, cfg, work_dir, **kwargs):
        calls["rip"].append(kwargs)
        folder = work_dir / "Album"
        folder.mkdir(parents=True)
        (folder / "Album.log").write_text(log_text)
        return folder, None

    def fake_rerip(cyanrip, cfg, album, work_dir, tracks, **kwargs):
        calls["rerip"].append((tracks, kwargs))
        log = album / "Album (secure re-rip).log"
        log.write_text(relog or "")
        if kwargs.get("fail"):
            raise rip.RipError("cyanrip failed")
        return [album / f"{n}.flac" for n in tracks], log, None
    monkeypatch.setattr(app.rip, "rip", fake_rip)
    monkeypatch.setattr(app.rip, "rerip", fake_rerip)
    monkeypatch.setattr(app.disc, "eject", lambda device: calls["eject"].append(device))
    return session, calls


def test_fast_rip_confirmed_by_accuraterip_is_ejected(tmp_path, monkeypatch, capsys):
    session, calls = fast_rip_session(tmp_path, monkeypatch, fast_log((1, ACCURATE, True), (2, ACCURATE, True)))
    feed_input(monkeypatch, ["r"])
    assert app.preview_and_rip(session, "/dev/sr0") is not None
    assert calls["rip"][0]["paranoia"] == "none" and calls["rip"][0]["eject"] is False
    assert calls["rerip"] == [] and calls["eject"] == ["/dev/sr0"]
    assert "AccurateRip confirms all 2 tracks" in capsys.readouterr().out


def test_fast_rip_rereads_unconfirmed_tracks_securely(tmp_path, monkeypatch, capsys):
    log = fast_log((1, ACCURATE, True), (2, NOT_FOUND, True), (3, ACCURATE, False))
    session, calls = fast_rip_session(tmp_path, monkeypatch, log,
                                      relog=fast_log((2, ACCURATE, True), (3, NOT_FOUND, True)))
    feed_input(monkeypatch, ["r"])
    app.preview_and_rip(session, "/dev/sr0")
    tracks, kwargs = calls["rerip"][0]
    assert tracks == [2, 3] and kwargs["eject"] is True
    assert calls["eject"] == []  # the re-rip ejects with -Q
    out = capsys.readouterr().out
    assert "does not confirm track(s) 2, 3; ripping them again securely" in out
    assert "track(s) 3 after the secure rip either" in out


def test_failed_rerip_keeps_the_fast_tracks_and_ejects(tmp_path, monkeypatch, capsys):
    log = fast_log((1, NOT_FOUND, True))
    session, calls = fast_rip_session(tmp_path, monkeypatch, log)
    real_rerip = app.rip.rerip
    monkeypatch.setattr(app.rip, "rerip", lambda *a, **k: real_rerip(*a, fail=True, **k))
    feed_input(monkeypatch, ["r"])
    assert app.preview_and_rip(session, "/dev/sr0") is not None
    assert calls["eject"] == ["/dev/sr0"]
    assert "track(s) 1 stay, unconfirmed" in capsys.readouterr().err


def test_secure_mode_and_discs_without_accuraterip(tmp_path, monkeypatch, capsys):
    session, calls = fast_rip_session(tmp_path, monkeypatch, "", accuraterip="not found")
    feed_input(monkeypatch, ["r"])
    app.preview_and_rip(session, "/dev/sr0")
    assert calls["rip"][0]["paranoia"] == "max" and calls["rip"][0]["eject"] is None
    assert "not in the AccurateRip database" in capsys.readouterr().out
    # p switches a disc in the database to secure, without a new preview.
    session, calls = fast_rip_session(tmp_path / "2", monkeypatch, "")
    previews = []
    monkeypatch.setattr(app.rip, "preview", lambda *a, **k: previews.append(1) or rip.Preview(0, [], accuraterip="found"))
    feed_input(monkeypatch, ["p", "r"])
    app.preview_and_rip(session, "/dev/sr0")
    assert calls["rip"][0]["paranoia"] == "max" and len(previews) == 1 and calls["rerip"] == []


# --- release / master --------------------------------------------------------
def test_master_is_always_a_source_build():
    ubuntu = {"ID": "ubuntu"}
    assert install.choose_route(ubuntu, platform="linux").kind == "package"
    assert install.choose_route(ubuntu, platform="linux", channel="master").kind == "source"
    assert "No cyanrip.exe of master" in install.choose_route({}, platform="win32", channel="master").description


def test_channel_is_validated(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"cyanrip": {"channel": "beta"}}', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="channel"):
        config.load_config(path)


def test_version_order():
    keys = sorted(["0.9.4", "0.9.3.1", "0.9.4-rc3", "0.10.0", "0.9.3"], key=install.version_key)
    assert keys == ["0.9.3", "0.9.3.1", "0.9.4-rc3", "0.9.4", "0.10.0"]


def test_installed_version_parses_cyanrip_v(monkeypatch):
    # Real output of master on 2026-10-11.
    monkeypatch.setattr(install.proc, "capture",
                        lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0, "cyanrip 0.9.4-rc3 (b40c1f0)\n", ""))
    assert install.installed_version("cyanrip") == install.Installed("0.9.4-rc3", "b40c1f0")
    monkeypatch.setattr(install.proc, "capture",
                        lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0, "cyanrip 0.9.3.1\n", ""))
    assert install.installed_version("cyanrip") == install.Installed("0.9.3.1", None)


def test_remote_info_is_parsed_and_cached(monkeypatch):
    calls = []

    def capture(cmd, timeout=10):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, LS_REMOTE, "")
    monkeypatch.setattr(install.proc, "capture", capture)
    remote = install.remote_info()
    assert remote.latest_tag == "v0.9.3.1"  # release candidates are not releases
    assert remote.master.startswith("b40c1f0")
    assert install.remote_info() == remote and len(calls) == 1  # second call from the cache
    install.remote_info(max_age=0)
    assert len(calls) == 2


def test_remote_info_failure_is_reported(monkeypatch):
    monkeypatch.setattr(install.proc, "capture",
                        lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access"))
    with pytest.raises(install.UpdateCheckError, match="unable to access"):
        install.remote_info()


REMOTE = install.Remote("v0.9.3.1", "b40c1f006a000a8bbd2d7ccc438a17006b9b71a0")


@pytest.mark.parametrize("channel, installed, self_built, expected", [
    ("master", install.Installed("0.9.3.1", None), False, ("is a release", True, False)),
    ("master", install.Installed("0.9.4-rc3", "b40c1f0"), True, None),           # on master
    ("master", install.Installed("0.9.4-rc3", "a1b2c3d"), True, ("new commits", True, True)),
    ("release", install.Installed("0.9.3", None), False, ("0.9.3.1 is out", False, True)),  # package
    ("release", install.Installed("0.9.3", "11fe094"), True, ("0.9.3.1 is out", True, True)),
    ("release", install.Installed("0.9.3.1", None), False, None),
    ("release", install.Installed("0.9.4-rc3", "b40c1f0"), True, None),          # master is newer
])
def test_update_offer(channel, installed, self_built, expected):
    offer = install.update_offer(channel, installed, REMOTE, self_built)
    if expected is None:
        assert offer is None
    else:
        text, can_build, daily = expected
        assert text in offer.message and offer.can_build == can_build and offer.daily == daily


def test_news_is_shown_once_a_day_but_mismatch_always():
    news = install.UpdateOffer("cyanrip 0.9.4 is out", can_build=True, daily=True)
    assert install.should_show(news) and not install.should_show(news)
    mismatch = install.UpdateOffer("is a release", can_build=True, daily=False)
    assert install.should_show(mismatch) and install.should_show(mismatch)


def test_master_prefers_the_local_build(tmp_path, monkeypatch):
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "cyanrip").write_text("")
    monkeypatch.setattr(tools.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/cyanrip")
    cfg = default_cfg()
    assert tools.find_cyanrip(cfg) == "/usr/bin/cyanrip"
    cfg["cyanrip"]["channel"] = "master"
    assert tools.find_cyanrip(cfg) == str(local / "cyanrip")


def test_failed_release_build_suggests_master(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install, "install_dependencies", lambda os_release: True)
    monkeypatch.setattr(install, "remote_info", lambda max_age=0: REMOTE)
    monkeypatch.setattr(install.proc, "run", lambda cmd, cwd=None: 1 if cmd[:2] == ["meson", "compile"] else 0)
    monkeypatch.setattr(install.proc, "capture", lambda cmd, timeout=10: None)
    feed_input(monkeypatch, [str(tmp_path / "cyanrip"), str(tmp_path / "build"), ""])
    assert install.build_from_source(default_cfg(), tmp_path / "config.json", {"ID": "fedora"}) is None
    assert '"channel": "master"' in capsys.readouterr().out


def test_step_0_offers_master_over_installed_release(tmp_path, monkeypatch):
    cfg = default_cfg()
    cfg["cyanrip"]["channel"] = "master"
    session = app.new_session(cfg, app.Options(), tmp_path / "config.json")
    session.cyanrip = "/usr/bin/cyanrip"
    monkeypatch.setattr(install, "remote_info", lambda: REMOTE)
    built = []
    monkeypatch.setattr(install, "build_from_source", lambda cfg, path, rel: built.append(path) or "/x/cyanrip")
    feed_input(monkeypatch, [""])  # build now? yes
    app.offer_cyanrip_update(session, install.Installed("0.9.3.1", None))
    assert built == [tmp_path / "config.json"] and session.cyanrip == "/x/cyanrip"


# --- measured offset ---------------------------------------------------------
def test_parse_offset_reads_cyanrips_result():
    lines = ["Offset of +6 confirmed (confidence: 3) in track 4, trying to confirm with another track",
             "Drive offset of +6 found (confidence: 4)!"]
    assert app.parse_offset(lines) == (6, 4)
    assert app.parse_offset(["Drive offset of -1164 found (confidence: 9)!"]) == (-1164, 9)
    assert app.parse_offset(["No track had AccuRip entry, cannot find offset!"]) is None


def fake_measurement(monkeypatch, lines, code=0):
    monkeypatch.setattr(app.proc, "run_tail", lambda cmd, cwd=None, lines_=None, **k: (code, lines))


def test_confident_measurement_is_saved_without_asking(tmp_path, monkeypatch):
    fake_measurement(monkeypatch, ["Data loaded, searching for offsets...",
                                   "Drive offset of +6 found (confidence: 4)!"])
    session = offset_session(tmp_path)
    feed_input(monkeypatch, ["f"])  # only the menu choice, no offset prompt
    assert app.ensure_offset(session, "/dev/sr0")
    assert json.loads(session.config_path.read_text(encoding="utf-8")) == {"cyanrip": {"offset": 6}}


def test_low_confidence_measurement_asks_first(tmp_path, monkeypatch):
    fake_measurement(monkeypatch, ["Drive offset of +6 found (confidence: 2)!"])
    session = offset_session(tmp_path)
    feed_input(monkeypatch, ["f", "", "0"])  # measure; Enter = back (the default); then 0 for this rip
    assert app.ensure_offset(session, "")
    assert session.cfg["cyanrip"]["offset"] == 0 and not session.config_path.exists()

    session = offset_session(tmp_path)
    feed_input(monkeypatch, ["f", "u"])  # measure; use it anyway
    assert app.ensure_offset(session, "")
    assert json.loads(session.config_path.read_text(encoding="utf-8")) == {"cyanrip": {"offset": 6}}


# --- folders on first use ----------------------------------------------------
def record_ask_path(monkeypatch, answers=None):
    """Replace the prefilled prompt; returns the (prompt, prefill) pairs asked."""
    asked = []
    answers = list(answers or [])

    def ask_path(prompt, prefill=""):
        asked.append((prompt, prefill))
        return answers.pop(0) if answers else prefill  # default: Enter accepts the suggestion
    monkeypatch.setattr(app.ui, "ask_path", ask_path)
    return asked


def test_first_use_asks_for_both_folders_and_saves_them(tmp_path, monkeypatch):
    dirs = tmp_path / "config-home" / "user-dirs.dirs"
    dirs.parent.mkdir(parents=True)
    dirs.write_text('XDG_DESKTOP_DIR="$HOME/Schreibtisch"\nXDG_MUSIC_DIR="$HOME/Musik"\n', encoding="utf-8")
    session = app.new_session(default_cfg(), app.Options(), tmp_path / "config.json")
    assert session.work_dir is None and session.library_dir is None
    asked = record_ask_path(monkeypatch, ["", str(tmp_path / "rips")])  # empty is refused
    app.ask_missing_folders(session)
    assert [prefill for _, prefill in asked] == ["~/Temp", "~/Temp", "~/Musik"]
    assert session.work_dir == tmp_path / "rips" and session.library_dir == Path.home() / "Musik"
    saved = json.loads(session.config_path.read_text(encoding="utf-8"))
    assert saved == {"work_dir": str(tmp_path / "rips"), "library_dir": "~/Musik"}


def test_folders_from_config_or_flags_are_not_asked(tmp_path, monkeypatch):
    cfg = default_cfg()
    cfg["work_dir"] = str(tmp_path / "w")  # e.g. from -w for this run
    session = app.new_session(cfg, app.Options(), tmp_path / "config.json")
    asked = record_ask_path(monkeypatch)
    app.ask_missing_folders(session)
    assert [p for p, _ in asked] == ["Music library (finished albums go here)"]
    assert "work_dir" not in json.loads(session.config_path.read_text(encoding="utf-8"))

    cfg["library_dir"] = str(tmp_path / "lib")
    asked = record_ask_path(monkeypatch)
    app.ask_missing_folders(app.new_session(cfg, app.Options(), tmp_path / "other.json"))
    assert asked == []


def test_music_dir_suggestion_falls_back_to_music():
    # The fixture points XDG_CONFIG_HOME at an empty folder: no user-dirs.dirs.
    assert config.music_dir_suggestion() == "~/Music"


# --- a rip running in another cdimport ---------------------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="the lock uses flock")
def test_running_rip_is_locked_and_left_alone(tmp_path, monkeypatch, capsys):
    session = offset_session(tmp_path)
    session.work_dir = tmp_path
    with rip.staging_dir(tmp_path) as staging:
        (staging / "Album").mkdir()
        (staging / "Album" / "01.flac").write_text("x")
        assert rip.in_use(staging)
        feed_input(monkeypatch, [])  # must not even ask
        app.offer_staging_cleanup(session)
        assert "leaving it alone" in capsys.readouterr().out
    assert not rip.lock_path(staging).exists() and rip.in_use(staging) is False
    assert not rip.lock_path(staging).exists()            # checking did not create it again
    feed_input(monkeypatch, ["n"])  # now it is a leftover: asked, and kept
    app.offer_staging_cleanup(session)
    assert staging.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the lock uses flock")
def test_staging_folder_is_locked_before_it_exists(tmp_path, monkeypatch):
    seen = []
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.name.startswith(STAGING_PREFIX):
            seen.append(rip.in_use(self))                 # what another cdimport would see
        return real_mkdir(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    with rip.staging_dir(tmp_path) as first, rip.staging_dir(tmp_path) as second:
        assert first != second and first.is_dir() and second.is_dir()   # the same second: another name
    assert seen == [True, True]


@pytest.mark.skipif(sys.platform == "win32", reason="the lock uses flock")
def test_lock_of_a_crashed_run_counts_as_free(tmp_path):
    staging = tmp_path / f"{STAGING_PREFIX}x"
    staging.mkdir()
    rip.lock_path(staging).write_text("12345\n")  # left behind, nobody holds it
    assert rip.in_use(staging) is False


def test_rip_holds_the_lock_while_cyanrip_runs(tmp_path, monkeypatch):
    seen = []

    def run_tail(cmd, cwd=None, lines=3, keep_bytes=8192):
        seen.append(rip.in_use(Path(cwd)))
        return fake_cyanrip(["Album/01.flac"])(cmd, cwd)
    monkeypatch.setattr(rip.proc, "run_tail", run_tail)
    folder, _ = rip.rip("cyanrip", default_cfg(), tmp_path)
    assert seen == [True]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Album"]  # no lock file left


# --- tools that do not start -------------------------------------------------
def test_run_reports_a_tool_that_cannot_start(tmp_path, capsys):
    launcher = tmp_path / "lyrembed"
    launcher.write_text("#!/nonexistent/python3.14\n")  # like a pipx venv after a Python upgrade
    launcher.chmod(0o755)
    assert proc.run([str(launcher)]) == proc.CANNOT_START
    assert "cannot start" in capsys.readouterr().err
    assert tools.start_problem([str(launcher), "--version"])
    assert tools.start_problem([sys.executable, "-c", "pass"]) is None


def test_broken_lyrembed_stops_with_a_resumable_state(tmp_path, monkeypatch, album_dir):
    session = app.new_session(default_cfg(), app.Options(), tmp_path / "config.json")
    session.lyrembed_problem = "bad interpreter - repair it with: pipx reinstall lyrembed"
    album = state.load(album_dir)
    album.mark("tags")
    feed_input(monkeypatch, [""])  # default: stop here
    with pytest.raises(app.Quit):
        app.lyrics_step(session, album)
    assert state.load(album_dir).next_step() == "lyrics"


# --- cover art ---------------------------------------------------------------
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
RELEASE = "5c580fb1-7c31-4785-afde-68098dc5caa2"
GROUP = "1fa07f09-6dc6-42ab-b7b1-967b3f0e8ba5"


def fake_archive(monkeypatch, release_images, group_images):
    """Answers shaped like the real ones for 때깔 (Killin' It) on 2026-10-11."""
    fetched = []

    def http_get(url, timeout=20):
        fetched.append(url)
        if url == f"{cover.CAA_URL}/release/{RELEASE}":
            return json.dumps({"images": release_images}).encode()
        if url.startswith(f"{cover.MB_URL}/release/{RELEASE}"):
            return json.dumps({"release-group": {"id": GROUP}}).encode()
        if url == f"{cover.CAA_URL}/release-group/{GROUP}":
            return json.dumps({"images": group_images}).encode()
        if url.endswith(".jpg"):
            return JPEG
        raise cover.NotFound(url)
    monkeypatch.setattr(cover, "http_get", http_get)
    return fetched


UNTYPED = {"types": [], "front": False, "image": "https://coverartarchive.org/release/r/1.jpg",
           "thumbnails": {"1200": "https://coverartarchive.org/release/r/1-1200.jpg"}}
GROUP_FRONT = {"types": ["Front"], "front": True, "image": "http://coverartarchive.org/release/o/2.jpg",
               "thumbnails": {"1200": "http://coverartarchive.org/release/o/2-1200.jpg"}}


def test_candidates_offer_untyped_image_and_group_cover(monkeypatch):
    fake_archive(monkeypatch, [UNTYPED], [GROUP_FRONT])
    found = cover.candidates(RELEASE)
    assert [c.description for c in found] == ["this release's only image (not marked as a cover)",
                                              "front cover of another edition of the album"]
    assert found[0].url.endswith("1-1200.jpg")
    assert found[1].url.startswith("https://") and found[1].page.startswith("https://")


def test_offer_cover_embeds_the_choice_once(tmp_path, monkeypatch):
    folder = tmp_path / "Album"
    folder.mkdir()
    make_flac(folder / "01.flac", musicbrainz_albumid=RELEASE)
    make_flac(folder / "02.flac", musicbrainz_albumid=RELEASE)
    fetched = fake_archive(monkeypatch, [UNTYPED], [GROUP_FRONT])
    feed_input(monkeypatch, ["2"])  # the other edition's front cover
    covertool.offer(folder)
    assert fetched[-1] == "https://coverartarchive.org/release/o/2-1200.jpg"
    from mutagen.flac import FLAC
    pictures = FLAC(folder / "01.flac").pictures
    assert len(pictures) == 1 and pictures[0].type == 3 and pictures[0].mime == "image/jpeg"
    assert (folder / "cover.jpg").read_bytes() == JPEG
    answers = feed_input(monkeypatch, [""])  # covered now: only keep-or-change, Enter keeps it
    covertool.offer(folder)
    assert answers == [] and len(FLAC(folder / "01.flac").pictures) == 1


def test_keep_or_change_links_the_embedded_cover(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "Album"
    folder.mkdir()
    make_flac(folder / "01.flac")
    cover.embed(tags.audio_files(folder), JPEG)
    seen = []
    monkeypatch.setattr(covertool.ui, "choose", lambda title, options, default, info=None: seen.append(
        list(config.runtime_dir().glob("cover-preview-*"))) or default)
    covertool.offer(folder)  # keep
    preview = config.runtime_dir() / f"cover-preview-{os.getpid()}.jpg"
    assert seen == [[preview]]  # there while the question is asked ...
    assert not preview.exists()  # ... and gone after the answer
    assert f"Current cover: {preview.as_uri()}" in capsys.readouterr().out
    assert oct(config.runtime_dir().stat().st_mode & 0o777) == "0o700"


def test_a_back_cover_alone_is_no_cover(tmp_path, monkeypatch):
    from mutagen.flac import FLAC, Picture
    folder = tmp_path / "Album"
    folder.mkdir()
    back_only = make_flac(folder / "01.flac")
    audio = FLAC(back_only)
    picture = Picture()
    picture.type, picture.mime, picture.data = 4, "image/jpeg", b"\xff\xd8\xff back"
    audio.add_picture(picture)
    audio.save()
    covered = make_flac(folder / "02.flac")
    cover.embed([covered], JPEG)
    assert cover.missing([back_only, covered]) == [back_only]
    assert cover.embed([back_only, covered], JPEG) == 1  # only the track without a front cover
    assert {p.type for p in FLAC(back_only).pictures} == {3, 4}


def test_cover_from_own_file_and_bad_files(tmp_path, monkeypatch):
    folder = tmp_path / "Album"
    folder.mkdir()
    make_flac(folder / "01.flac")  # no release ID and no album tag: nothing to look up
    monkeypatch.setattr(cover, "http_get", lambda url, timeout=20: pytest.fail("no lookup without an ID"))
    (tmp_path / "notes.txt").write_text("not a picture")
    (tmp_path / "front.jpg").write_bytes(JPEG)
    (folder / "cover.jpg").write_bytes(b"theirs")  # an existing cover file stays
    answers = [str(tmp_path / "notes.txt"), str(tmp_path / "front.jpg")]
    monkeypatch.setattr(covertool.ui, "ask_path", lambda prompt, prefill="": answers.pop(0))
    feed_input(monkeypatch, ["f", "f"])  # the text file is refused, then the JPEG
    covertool.offer(folder)
    from mutagen.flac import FLAC
    assert len(FLAC(folder / "01.flac").pictures) == 1
    assert (folder / "cover.jpg").read_bytes() == b"theirs"


# --- cdimport-cover ----------------------------------------------------------
def test_search_by_tags_when_there_is_no_release_id(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "Floral Shoppe"
    folder.mkdir()
    make_flac(folder / "01.flac", album='Floral "Shoppe"', albumartist="Macintosh Plus")
    asked = []

    def http_get(url, timeout=20):
        asked.append(url)
        if "/release-group/?query=" in url:
            return json.dumps({"release-groups": [
                {"id": "g1", "title": "Floral Shoppe", "first-release-date": "2011-12-09", "primary-type": "Album",
                 "artist-credit": [{"name": "Macintosh Plus", "joinphrase": ""}]}]}).encode()
        if url == f"{cover.CAA_URL}/release-group/g1":
            return json.dumps({"images": [GROUP_FRONT]}).encode()
        if url.endswith(".jpg"):
            return JPEG
        raise cover.NotFound(url)
    monkeypatch.setattr(cover, "http_get", http_get)
    feed_input(monkeypatch, ["", ""])  # which album: the first; cover: the first
    covertool.offer(folder)
    assert "https://musicbrainz.org/release-group/g1" in capsys.readouterr().out
    import urllib.parse
    query = urllib.parse.unquote(asked[0].split("query=")[1].split("&")[0])
    assert query == 'releasegroup:"Floral \\"Shoppe\\"" AND artist:"Macintosh Plus"'
    from mutagen.flac import FLAC
    assert len(FLAC(folder / "01.flac").pictures) == 1


def test_album_folders_and_command_line(tmp_path, monkeypatch, capsys):
    library = tmp_path / "lib"
    (library / "Artist" / "A").mkdir(parents=True)
    (library / "Artist" / "B").mkdir(parents=True)
    make_flac(library / "Artist" / "A" / "01.flac")
    make_flac(library / "Artist" / "B" / "01.flac")
    assert covertool.album_folders([library], recursive=False) == []
    assert covertool.album_folders([library], recursive=True) == [library / "Artist" / "A", library / "Artist" / "B"]

    assert covertool.main([str(library), "--color", "never"]) == 2
    assert "add -r" in capsys.readouterr().err
    offered = []
    monkeypatch.setattr(covertool, "offer", lambda folder, release: offered.append((folder.name, release)))
    assert covertool.main([str(library), "-r", "--color", "never"]) == 0
    assert offered == [("A", ""), ("B", "")]
    with pytest.raises(SystemExit):  # --release names one album
        covertool.main([str(library), "-r", "--release", "x"])
    offered.clear()
    assert covertool.main([str(library / "Artist" / "A"), "--release", "abc"]) == 0
    assert offered == [("A", "abc")]


def test_musicbrainz_requests_are_spaced(monkeypatch):
    sleeps = []
    clock = [100.0]
    monkeypatch.setattr(cover.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cover.time, "sleep", lambda s: sleeps.append(round(s, 2)))
    def no_network(request, timeout=20):
        raise OSError("tests never touch the network")
    monkeypatch.setattr(cover.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(cover, "_last_mb_request", 99.5)
    with pytest.raises(cover.CoverError, match="never touch"):
        cover.http_get(f"{cover.MB_URL}/release/x")
    assert sleeps == [0.6]


def test_search_drops_an_edition_note_when_nothing_is_found(monkeypatch):
    queries = []

    def http_get(url, timeout=20):
        import urllib.parse
        query = urllib.parse.unquote(url.split("query=")[1].split("&")[0])
        queries.append(query)
        groups = [{"id": "g1", "title": "Floral Shoppe"}] if "(Remastered)" not in query else []
        return json.dumps({"release-groups": groups}).encode()
    monkeypatch.setattr(cover, "http_get", http_get)
    assert cover.search_albums("Floral Shoppe (Remastered)", "Macintosh Plus") == [("g1", "Floral Shoppe - ?")]
    assert queries == ['releasegroup:"Floral Shoppe (Remastered)" AND artist:"Macintosh Plus"',
                       'releasegroup:"Floral Shoppe" AND artist:"Macintosh Plus"']


def test_busy_musicbrainz_is_retried(monkeypatch):
    import io
    import urllib.error
    answers = [503, 503, 200]
    monkeypatch.setattr(cover.time, "sleep", lambda s: None)

    def urlopen(request, timeout=20):
        code = answers.pop(0)
        if code != 200:
            raise urllib.error.HTTPError(request.full_url, code, "busy", {}, None)
        return io.BytesIO(b"{}")
    monkeypatch.setattr(cover.urllib.request, "urlopen", urlopen)
    assert cover.http_get(f"{cover.MB_URL}/release/x") == b"{}"
    answers[:] = [503, 503, 503]
    with pytest.raises(cover.CoverError, match="busy"):
        cover.http_get(f"{cover.MB_URL}/release/x")


@pytest.mark.parametrize("done", [["rip"], ["rip", "tags"], ["rip", "tags", "lyrics"]])
def test_reopened_album_gets_the_cover_offer_at_any_step(tmp_path, monkeypatch, done):
    folder = tmp_path / "Album"
    folder.mkdir()
    make_flac(folder / "01.flac", artist="IU", title="Blueming")
    album = state.AlbumState(folder, done=list(done))
    album.save()
    offered = []
    monkeypatch.setattr(app, "offer_cover", lambda f: offered.append(f))
    session = app.new_session(default_cfg(), app.Options(), tmp_path / "config.json")
    monkeypatch.setattr(app, "tags_step", lambda s, a: False)  # stop right after the offer
    monkeypatch.setattr(app, "lyrics_step", lambda s, a: False)
    monkeypatch.setattr(app, "finish_step", lambda s, a: False)
    app.continue_album(session, state.load(folder))
    assert offered == [folder]


def test_changing_a_cover_keeps_other_pictures(tmp_path, monkeypatch):
    from mutagen.flac import FLAC, Picture
    folder = tmp_path / "Album"
    folder.mkdir()
    track = make_flac(folder / "01.flac", musicbrainz_albumid=RELEASE)
    audio = FLAC(track)
    for kind, data in ((3, b"\xff\xd8\xff old front"), (4, b"\xff\xd8\xff back")):
        picture = Picture()
        picture.type, picture.mime, picture.data = kind, "image/jpeg", data
        audio.add_picture(picture)
    audio.save()
    (folder / "cover.jpg").write_bytes(b"old")
    (folder / "cover.png").write_bytes(b"older")
    fake_archive(monkeypatch, [UNTYPED], [GROUP_FRONT])
    feed_input(monkeypatch, ["c", "1"])  # change it; this release's image
    covertool.offer(folder)
    pictures = {p.type: p.data for p in FLAC(track).pictures}
    assert pictures == {3: JPEG, 4: b"\xff\xd8\xff back"}  # front swapped, back cover kept
    assert (folder / "cover.jpg").read_bytes() == JPEG and not (folder / "cover.png").exists()


def test_keeping_is_the_default_and_change_can_be_abandoned(tmp_path, monkeypatch):
    folder = tmp_path / "Album"
    folder.mkdir()
    make_flac(folder / "01.flac")  # no ID, no album tag: only a file could be chosen
    covertool.cover.embed([folder / "01.flac"], JPEG)
    monkeypatch.setattr(cover, "http_get", lambda url, timeout=20: pytest.fail("nothing to look up"))
    feed_input(monkeypatch, ["c", ""])  # change; then Enter = keep the current cover
    covertool.offer(folder)
    from mutagen.flac import FLAC
    assert FLAC(folder / "01.flac").pictures[0].data == JPEG


# --- rip logs stay out of the library ----------------------------------------
def test_log_is_kept_in_the_working_folder_not_the_library(tmp_path, monkeypatch, album_dir, capsys):
    (album_dir / "Album.log").write_text("AccurateRip: accurately ripped")
    (album_dir / "Album.cue").write_text("FILE ...")
    (tmp_path / "work" / "logs").mkdir()
    (tmp_path / "work" / "logs" / "Album.log").write_text("an older rip")
    feed_input(monkeypatch, ["c", "", ""])  # tags: continue; finish: move; no other run
    assert run_cli(tmp_path, monkeypatch, [str(album_dir), "--lyrics", "skip"]) == 0
    moved = tmp_path / "lib" / "IU" / "Album"
    assert sorted(p.name for p in moved.iterdir()) == ["01.flac", "Album.cue"]
    logs = tmp_path / "work" / "logs"
    assert (logs / "Album.log").read_text() == "an older rip"  # never overwritten
    assert (logs / "Album (2).log").read_text() == "AccurateRip: accurately ripped"
    assert "Album (2).log" in capsys.readouterr().out  # the summary says where it went


# --- romanization in automatic mode ------------------------------------------
def test_romanize_flag_only_in_automatic_mode(tmp_path):
    cfg = default_cfg()
    assert lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "auto", True)[2:] == ["--auto", "--romanize"]
    assert lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "auto", False)[2:] == ["--auto", "--no-romanize"]
    assert lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "auto", None)[2:] == ["--auto"]
    assert lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "interactive", True)[2:] == []


def _answers(by_flag):
    """A fake proc.capture that answers lyrembed's --version and --help."""
    def capture(cmd, timeout=10):
        text = by_flag.get(cmd[-1])
        return None if text is None else subprocess.CompletedProcess(cmd, 0, text, "")
    return capture


def test_probe_reads_lyrembeds_help(monkeypatch):
    calls = []
    menus = "  --auto ...\n  --romanize  in --auto mode ..."
    for usage, page in ((menus + "\n  --no-browser  print the page's address ...", True), (menus, False)):
        # The version number is not asked.
        fake = _answers({"--version": "lyrembed 1.0.0\n", "--help": usage})
        monkeypatch.setattr(lyrics.proc, "capture", lambda cmd, timeout=10: calls.append(cmd[-1]) or fake(cmd))
        assert lyrics.probe("lyrembed") == lyrics.Features(page=page, romanize=True), usage
    assert calls == ["--help", "--help"]   # one start each
    for usage in ("  --dry-run ...", "", None):   # None: does not start
        monkeypatch.setattr(lyrics.proc, "capture", _answers({"--help": usage}))
        assert lyrics.probe("lyrembed") == lyrics.Features()


@pytest.mark.parametrize("page", [True, False])
def test_interactive_lyrics_say_how_to_get_back(tmp_path, monkeypatch, capsys, album_dir, page):
    session = app.new_session(default_cfg(), app.Options(lyrics="ask"), tmp_path / "config.json")
    session.lyrembed, session.lyrembed_page = "lyrembed", page
    monkeypatch.setattr(app.lyrics, "run_lyrembed", lambda lyr, cfg, folder, mode, romanize=None, return_url='': lyrics.EXIT_OK)
    album = state.load(album_dir)
    album.mark("tags")
    feed_input(monkeypatch, ["i"])
    assert app.lyrics_step(session, album)
    out = capsys.readouterr().out
    assert ("on lyrembed's page in the browser" in out) is page
    assert ("Quit it there, or close the tab, to continue here." in out) is page


@pytest.mark.parametrize("answer, expected", [("", None), ("y", True), ("n", False)])
def test_automatic_lyrics_asks_about_romanization(tmp_path, monkeypatch, album_dir, answer, expected):
    session = app.new_session(default_cfg(), app.Options(lyrics="auto"), tmp_path / "config.json")
    session.lyrembed, session.lyrembed_romanize = "lyrembed", True
    passed = []
    monkeypatch.setattr(app.lyrics, "run_lyrembed",
                        lambda lyr, cfg, folder, mode, romanize=None, return_url='': passed.append(romanize) or lyrics.EXIT_OK)
    album = state.load(album_dir)
    album.mark("tags")
    feed_input(monkeypatch, [answer, "c"])  # Enter = lyrembed's own setting; then continue without lyrics
    assert app.lyrics_step(session, album)
    assert passed == [expected]


def test_old_lyrembed_is_not_asked_and_gets_no_flag(tmp_path, monkeypatch, album_dir):
    session = app.new_session(default_cfg(), app.Options(lyrics="auto"), tmp_path / "config.json")
    session.lyrembed = "lyrembed"  # lyrembed_romanize stays False
    passed = []
    monkeypatch.setattr(app.lyrics, "run_lyrembed",
                        lambda lyr, cfg, folder, mode, romanize=None, return_url='': passed.append(romanize) or lyrics.EXIT_OK)
    album = state.load(album_dir)
    album.mark("tags")
    feed_input(monkeypatch, ["c"])  # no lyrics found: continue without them
    assert app.lyrics_step(session, album) and passed == [None]


@pytest.mark.parametrize("batch_code, found, answers, modes, says", [
    # Every track got lyrics: offer to check them.
    (lyrics.EXIT_OK, True, ["l"], ["auto", "interactive"], "Every track has lyrics (1)"),
    # lyrembed exits 0 although it found nothing: cdimport sees it in the tags.
    (lyrics.EXIT_OK, False, [""], ["auto", "interactive"], "1 of 1 tracks have no lyrics"),
    # Exit 1 means files it could not write; continuing keeps what it embedded.
    (lyrics.EXIT_BATCH_FAILURES, False, ["c"], ["auto"], "could not read or write some files"),
])
def test_automatic_lyrics_are_checked_in_the_tags(tmp_path, monkeypatch, capsys, album_dir,
                                                   batch_code, found, answers, modes, says):
    session = app.new_session(default_cfg(), app.Options(lyrics="auto"), tmp_path / "config.json")
    session.lyrembed = "lyrembed"
    codes = [batch_code, lyrics.EXIT_OK]
    called = []

    def fake_run(lyr, cfg, folder, mode, romanize=None, return_url=''):
        called.append(mode)
        if mode == "auto" and found:
            make_flac(folder / "01.flac", artist="IU", title="Blueming", lyrics="[00:01.00]la la")
        return codes.pop(0)
    monkeypatch.setattr(app.lyrics, "run_lyrembed", fake_run)
    album = state.load(album_dir)
    album.mark("tags")
    left = feed_input(monkeypatch, answers)
    assert app.lyrics_step(session, album)
    assert left == [] and called == modes
    assert "lyrics" in state.load(album_dir).done
    out = capsys.readouterr().out
    assert says in out and ("01.flac" in out) == (not found)


def test_lyrics_in_the_tags(tmp_path):
    with_lyrics = make_flac(tmp_path / "1.flac", lyrics="la la")
    synced = make_flac(tmp_path / "2.flac", syncedlyrics="[00:01.00]la")
    empty = make_flac(tmp_path / "3.flac", lyrics="  ")
    plain = make_flac(tmp_path / "4.flac", title="x")
    assert lyrics.without_lyrics([with_lyrics, synced, empty, plain]) == [empty, plain]
    (tmp_path / "5.flac").write_bytes(b"fLaC garbage")
    with pytest.raises(tags.TagReadError):
        lyrics.without_lyrics([tmp_path / "5.flac"])


def test_albums_lyrembed_cannot_tag_skip_the_lyrics(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "Album"
    folder.mkdir()
    (folder / "01.wav").write_bytes(b"RIFF")
    session = app.new_session(default_cfg(), app.Options(lyrics="auto"), tmp_path / "config.json")
    session.lyrembed = "lyrembed"
    monkeypatch.setattr(app.lyrics, "run_lyrembed", lambda *a, **k: pytest.fail("lyrembed would exit 2"))
    album = state.load(folder)
    assert app.lyrics_step(session, album)
    assert "lyrics" in state.load(folder).skipped
    assert "only handles FLAC, MP3, Ogg, Opus and M4A" in capsys.readouterr().out


# --- front ends other than the terminal ---------------------------------------
class RecordingFront(ui.Front):
    """Answers questions from a list and records everything it is handed."""

    def __init__(self, answers=(), pty=True):
        self.answers, self.pty = list(answers), pty
        self.messages, self.questions, self.output = [], [], b""

    def message(self, kind, text="", **details):
        self.messages.append((kind, text, details))

    def tool_output(self, data):
        self.output += data

    def wants_pty(self):
        return self.pty

    def _answer(self, kind, prompt, **details):
        self.questions.append((kind, prompt, details))
        if not self.answers:
            raise AssertionError(f"unexpected question: {prompt!r}")
        return self.answers.pop(0)

    def ask(self, prompt, default=""):
        return self._answer("ask", prompt, default=default)

    def confirm(self, prompt, default=True):
        return self._answer("confirm", prompt, default=default)

    def choose(self, prompt, options, default=None, info=None):
        return self._answer("choose", prompt, options=options, default=default, info=info)

    def ask_path(self, prompt, prefill=""):
        return self._answer("ask_path", prompt, prefill=prefill)

    def wait_for_enter(self, prompt):
        self._answer("wait", prompt)


@pytest.fixture
def front():
    """Install a RecordingFront; the terminal is back after the test."""
    def install(answers=(), pty=True):
        made = RecordingFront(answers, pty)
        ui.use(made)
        return made
    yield install
    ui.use(None)


def test_a_front_end_gets_the_questions_and_the_terminal_nothing(tmp_path, capsys, front):
    session = app.new_session(default_cfg(), app.Options(), tmp_path / "config.json")
    session.cyanrip = "cyanrip"
    recorder = front(["0"])                       # "use 0 for this rip only"
    assert app.ensure_offset(session, "/dev/sr0")
    assert session.cfg["cyanrip"]["offset"] == 0 and session.one_time_offset
    kind, prompt, details = recorder.questions[0]
    assert kind == "choose" and prompt == "Offset" and details["default"] == "f"
    assert [key for key, _ in details["options"]] == ["f", "e", "0", "b"]
    assert any(kind == "warn" and "read offset" in text for kind, text, _ in recorder.messages)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("pty", [True, False])
def test_tool_output_goes_to_the_front_end(capsys, front, pty):
    recorder = front(pty=pty)
    code, tail = proc.run_tail([sys.executable, "-c", "print('reading track 1'); print('done')"])
    assert code == 0 and tail == ["reading track 1", "done"]
    assert b"reading track 1" in recorder.output and capsys.readouterr().out == ""


# --- the browser page ----------------------------------------------------------
import http.client  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from cdimport import web  # noqa: E402


def _wait_for_question(front, timeout=5.0):
    deadline = time.monotonic() + timeout
    while front.question is None:
        assert time.monotonic() < deadline, "no question came"
        time.sleep(0.01)
    return front.question


def _in_thread(call):
    result = []
    thread = threading.Thread(target=lambda: result.append(call()), daemon=True)
    thread.start()
    return thread, result


def test_web_questions_wait_for_a_checked_answer():
    front = web.WebFront()
    thread, result = _in_thread(lambda: front.choose("Rip this disc", [("r", "rip"), ("q", "quit")], default="r"))
    question = _wait_for_question(front)
    assert question["options"] == [["r", "rip"], ["q", "quit"]] and question["default"] == "r"
    with pytest.raises(web.ApiError):
        front.answer(question["id"], "x")                 # not an option
    with pytest.raises(web.ApiError):
        front.answer(question["id"] + 1, "r")             # not the open question
    front.answer(question["id"], "q")
    thread.join(2)
    assert result == ["q"] and front.question is None
    assert front.events[-1]["kind"] == "answer" and front.events[-1]["text"] == "quit"

    thread, result = _in_thread(lambda: front.ask("Album", "Night Lines"))
    front.answer(_wait_for_question(front)["id"], "  ")   # empty: the default, as in the terminal
    thread.join(2)
    assert result == ["Night Lines"]


def test_web_stop_ends_the_open_question_and_every_later_one():
    front = web.WebFront()
    caught = []

    def flow():
        for _ in range(2):                                 # the flow's next question after a failed step
            try:
                front.confirm("Delete this folder?", default=False)
            except KeyboardInterrupt as exc:
                caught.append(exc)
    thread, _ = _in_thread(flow)
    _wait_for_question(front)
    assert front.stop()
    thread.join(2)
    assert len(caught) == 2 and all(isinstance(exc, web.Cancelled) for exc in caught)
    assert not front.stop()                               # nothing open any more


def test_waiting_for_a_disc_on_the_page_ends_on_stop(monkeypatch):
    monkeypatch.setattr(app.disc, "find_drives", lambda: [])
    front = web.WebFront()
    ui.use(front)
    try:
        thread, result = _in_thread(lambda: app.wait_for_disc(""))
        time.sleep(0.05)
        stopped = time.monotonic()
        front.stop()                                      # the page's "Stop cdimport"
        thread.join(2)
        assert result == [None] and time.monotonic() - stopped < 1   # not after the next poll
    finally:
        ui.use(None)


def test_after_ctrl_c_on_the_page_the_tool_gets_no_second_one(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(web.proc, "interrupt_tool", lambda: sent.append("SIGINT") or True)
    run = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"))
    run.stop(interrupt_tool=False)                        # the terminal's Ctrl+C reached it already
    assert sent == []
    with pytest.raises(web.Cancelled):
        run.front.confirm("Rip again?")                   # after the tool: the flow ends, nobody asks
    run.stop()
    assert sent == ["SIGINT"]


def test_terminal_interrupts_tools_only_in_the_foreground(monkeypatch):
    class Terminal:
        def fileno(self):
            return 0
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(proc.os, "tcgetpgrp", lambda fd: proc.os.getpgrp())
    assert proc.terminal_interrupts_tools()
    monkeypatch.setattr(proc.os, "tcgetpgrp", lambda fd: proc.os.getpgrp() + 1)   # in the background
    assert not proc.terminal_interrupts_tools()

    def no_terminal(fd):
        raise OSError(25, "Inappropriate ioctl for device")
    monkeypatch.setattr(proc.os, "tcgetpgrp", no_terminal)
    assert not proc.terminal_interrupts_tools()


def test_a_line_break_split_between_reads_keeps_the_line():
    front = web.WebFront()
    front.message("tool_start", "lyrembed DIR --no-browser", name="lyrembed", handoff=True)
    front.tool_output(b"lyrembed is running at http://127.0.0.1:43851/?token=abc\r")   # the pty's \r\n, cut
    front.tool_output(b"\nOpen that address\r\n")
    tool = front.events[0]
    assert tool["lines"] == ["lyrembed is running at http://127.0.0.1:43851/?token=abc", "Open that address"]
    assert [e["url"] for e in front.events if e["kind"] == "handoff"] == ["http://127.0.0.1:43851/?token=abc"]


def test_the_state_sent_is_a_copy():
    front = web.WebFront()
    front.message("tool_start", "ripping", name="cyanrip")
    front.tool_output(b"Track 1\n")
    sent = front.snapshot(0)
    front.tool_output(b"Track 2\nprogress 5%")                  # the flow goes on while it is sent
    assert sent["events"][0]["lines"] == ["Track 1"] and sent["events"][0]["partial"] == ""


def test_web_tool_output_keeps_lines_and_the_newest_progress():
    front = web.WebFront()
    front.message("tool_start", "ripping fast", name="cyanrip")
    front.tool_output(b"Track 1\nprogress 10%\rprogress 5")
    tool = front.events[-1]
    assert tool["lines"] == ["Track 1"] and tool["partial"] == "progress 5"
    front.tool_output(b"0%\r")                             # the line is redrawn
    assert tool["partial"] == "progress 50%"
    front.tool_output("\x1b[32mprogress 100%\x1b[0m\nVerz\xc3".encode("latin-1") + b"\xa4ichnis\n")
    assert tool["lines"] == ["Track 1", "progress 100%", "Verzäichnis"] and tool["partial"] == ""
    front.tool_output(b"almost done\r")
    front.message("tool_end", name="cyanrip", code=0)
    assert tool["lines"][-1] == "almost done" and not tool["running"] and tool["code"] == 0


def test_web_run_goes_through_the_flow_and_ends_with_its_code(monkeypatch, tmp_path):
    def flow(session):
        ui.step(1, "Disc")
        if ui.choose("Rip this disc", [("r", "rip"), ("q", "quit")], default="r") == "q":
            raise app.Quit(0)
        ui.success("Ripped.")
        return 0

    monkeypatch.setattr(app, "run", flow)
    run = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"))
    try:
        run.start()
        run.front.answer(_wait_for_question(run.front)["id"], "r")
        run.thread.join(2)
        assert run.front.finished == 0
        kinds = [e["kind"] for e in run.front.events]
        assert kinds == ["step", "answer", "success"]
        run = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"))
        run.start()
        _wait_for_question(run.front)
        run.stop()                                         # the page's "Stop cdimport"
        run.thread.join(2)
        assert run.front.finished == 130 and run.front.events[-1]["text"] == "Stopped."
    finally:
        ui.use(None)


@pytest.fixture
def page(monkeypatch, tmp_path):
    """A running page whose flow asks one question."""
    def flow(session):
        return 0 if ui.confirm("Continue?") else 1

    monkeypatch.setattr(app, "run", flow)
    run = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"))
    server = web._Server(run, "test-token")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield server
    if run.started:
        run.stop()
        run.thread.join(2)
    ui.use(None)
    server.shutdown()
    server.server_close()


def _http(server, method, path, body=None, headers=None, cookie=True):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    sent = {"Host": f"127.0.0.1:{server.port}"}
    if cookie:
        sent["Cookie"] = f"{web.cookie_name(server.port)}=test-token"
    if body is not None:
        body = json.dumps(body).encode()
        sent.update({"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.port}"})
    sent.update(headers or {})
    conn.request(method, path, body=body, headers=sent)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, json.loads(data) if data and response.getheader("Content-Type", "").startswith("application/json") else data


def test_web_page_needs_its_token_host_and_origin(page):
    assert _http(page, "GET", "/", cookie=False)[0].status == 403
    response, _ = _http(page, "GET", "/?token=test-token", cookie=False)
    assert response.status == 303 and "HttpOnly" in response.getheader("Set-Cookie")
    response, body = _http(page, "GET", "/")
    assert response.status == 200 and b"/static/app.js" in body and "default-src 'self'" in response.getheader("Content-Security-Policy")
    assert _http(page, "GET", "/api/state", headers={"Host": f"evil.example:{page.port}"})[0].status == 400
    assert _http(page, "POST", "/api/quit", {}, headers={"Origin": "http://evil.example"})[0].status == 403
    assert _http(page, "POST", "/api/quit", {}, headers={"Content-Type": "text/plain"})[0].status == 415
    for path in ("/static/../web.py", "/static/%2e%2e/web.py"):
        assert _http(page, "GET", path)[0].status == 404


def test_web_page_answers_the_flow(page):
    assert not page.run.started                           # nothing runs before the page asks
    deadline = time.monotonic() + 5
    state = {}
    while not state.get("question"):
        assert time.monotonic() < deadline
        state = _http(page, "GET", "/api/state?since=0")[1]
    assert state["question"]["kind"] == "confirm" and state["question"]["prompt"] == "Continue?"
    assert _http(page, "POST", "/api/answer", {"id": state["question"]["id"], "value": "yes"})[0].status == 400
    assert _http(page, "POST", "/api/answer", {"id": state["question"]["id"], "value": False})[0].status == 200
    page.run.thread.join(2)
    assert page.run.front.finished == 1
    later = _http(page, "GET", f"/api/state?since={state['rev']}")[1]
    assert later["finished"] == 1 and later["question"] is None


def test_web_flags(capsys):
    for argv in (["--no-browser"], ["--web", "--install-cyanrip"]):
        with pytest.raises(SystemExit):
            cli.main(argv)


def test_a_running_tool_can_be_stopped_like_ctrl_c(front):
    front()
    script = ("import signal, sys, time\n"
              "signal.signal(signal.SIGINT, lambda *a: (print('log finished'), sys.exit(3)))\n"
              "print('ripping', flush=True)\n"
              "time.sleep(10)\n")
    stopper = threading.Timer(0.5, proc.interrupt_tool)
    stopper.start()
    code, tail = proc.run_tail([sys.executable, "-c", script])
    assert code == 3 and tail[-1] == "log finished"
    assert not proc.interrupt_tool()                       # nothing runs any more


# --- the page's tag editor and pictures ------------------------------------------
from cdimport import tageditor  # noqa: E402


def _ripped_album(tmp_path, titles=("Opening", "Paper Moon (feat. Guest)"), album="Night: Lines"):
    """Two tracks as cyanrip names them, with a cue sheet and a log."""
    import mutagen.flac

    folder = tmp_path / (tageditor.cyanrip_name(album) + " (Limited)")
    folder.mkdir()
    for n, title in enumerate(titles, 1):
        path = make_flac(folder / f"0{n} - {tageditor.cyanrip_name(title)}.flac", title=title, artist="Sample Artist",
                         albumartist="Sample Artist", album=album, tracknumber=str(n),
                         musicbrainz_trackid=f"id-{n}")
        audio = mutagen.flac.FLAC(path)
        audio["album artist"] = "Sample Artist"            # a second spelling some taggers add
        audio.save()
    cue = folder / f"{tageditor.cyanrip_name(album)}.cue"
    cue.write_text("".join(f'FILE "0{n} - {tageditor.cyanrip_name(t)}.flac" WAVE\n  TRACK 0{n} AUDIO\n'
                           for n, t in enumerate(titles, 1)), encoding="utf-8")
    (folder / f"{tageditor.cyanrip_name(album)}.log").write_text("log, checksum ABC\n", encoding="utf-8")
    return folder


def test_names_are_made_as_cyanrip_makes_them():
    assert tageditor.cyanrip_name('Say "Hi" / Bye? <3') == "Say “Hi” ∕ Bye？ ‹3"
    assert tageditor.credit_from(["A"]) == "A" and tageditor.credit_from(["A", "B", "C"]) == "A, B & C"


def test_tag_editor_saves_renames_and_follows_in_the_cue_sheet(tmp_path):
    import mutagen.flac

    folder = _ripped_album(tmp_path)
    album = tageditor.read_album(folder)
    assert album["album"] == "Night: Lines" and album["albumArtist"] == "Sample Artist" and album["differing"] == []
    assert [t["artists"] for t in album["tracks"]] == [[], []]
    album["album"] = "Night Lines"
    album["albumArtist"] = "Sample Artist & Friend"
    second = album["tracks"][1]
    second.update(title="Paper Moon", artist="Sample Artist feat. Guest", artists=["Sample Artist", "Guest"])
    plan = tageditor.plan(folder, album)
    assert plan["problem"] == "" and plan["folderName"] == "Night Lines (Limited)"
    assert [f["newFile"] for f in plan["files"]] == [None, "02 - Paper Moon.flac"]

    result = tageditor.save(folder, album)
    moved = tmp_path / "Night Lines (Limited)"
    assert result["folder"] == str(moved) and result["folderRenamed"] and not folder.exists()
    assert sorted(p.name for p in moved.iterdir()) == ["01 - Opening.flac", "02 - Paper Moon.flac",
                                                        "Night Lines.cue", "Night Lines.log"]
    assert 'FILE "02 - Paper Moon.flac" WAVE' in (moved / "Night Lines.cue").read_text(encoding="utf-8")
    assert (moved / "Night Lines.log").read_text(encoding="utf-8") == "log, checksum ABC\n"   # never changed
    audio = mutagen.flac.FLAC(moved / "02 - Paper Moon.flac")
    assert audio["artist"] == ["Sample Artist feat. Guest"] and audio["artists"] == ["Sample Artist", "Guest"]
    # A changed album artist leaves one spelling, not a stale second one.
    assert audio["album"] == ["Night Lines"] and "album artist" not in audio
    assert audio["albumartist"] == ["Sample Artist & Friend"]
    assert audio["musicbrainz_trackid"] == ["id-2"]                                            # untouched


def test_tag_editor_keeps_names_cyanrip_did_not_make_and_swaps_safely(tmp_path):
    folder = _ripped_album(tmp_path, titles=("A", "B"))
    (folder / "02 - B.flac").rename(folder / "track two.flac")         # renamed by hand earlier
    album = tageditor.read_album(folder)
    album["tracks"][1]["title"] = "B2"
    assert tageditor.save(folder, album)["kept"] == ["track two.flac"]
    assert (folder / "track two.flac").exists()

    other = tmp_path / "other"
    other.mkdir()
    folder = _ripped_album(other, titles=("A", "B"))
    album = tageditor.read_album(folder)
    album["tracks"][0]["title"], album["tracks"][1]["title"] = "B", "A"
    tageditor.save(folder, album)                                       # 01 - A -> 01 - B, 02 - B -> 02 - A
    assert sorted(p.name for p in folder.glob("*.flac")) == ["01 - B.flac", "02 - A.flac"]


def test_tag_editor_refuses_before_writing_anything(tmp_path):
    import mutagen.flac

    folder = _ripped_album(tmp_path, titles=("A", "B"))
    album = tageditor.read_album(folder)
    album["tracks"][0]["title"] = "Changed"
    album["tracks"][1]["artist"] = ""
    assert "needs a title and an artist" in tageditor.plan(folder, album)["problem"]
    with pytest.raises(tageditor.TagEditError):
        tageditor.save(folder, album)
    assert mutagen.flac.FLAC(folder / "01 - A.flac")["title"] == ["A"]                         # nothing written
    album = tageditor.read_album(folder)
    album["tracks"][0]["title"] = "B"                                   # 01 - B next to 02 - B is fine ...
    (folder / "01 - B.flac").mkdir()                                    # ... unless something of that name is there
    with pytest.raises(tageditor.TagEditError, match="already there"):
        tageditor.save(folder, album)


def test_the_page_serves_only_pictures_the_flow_named(tmp_path, monkeypatch):
    picture = tmp_path / "cover.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0 jpeg")
    fetched = []
    monkeypatch.setattr(cover, "http_get", lambda url: fetched.append(url) or b"\x89PNG\r\n\x1a\n png")
    front = web.WebFront()
    thread, result = _in_thread(lambda: front.choose("Cover", [("1", "front cover"), ("k", "keep it")], default="k",
                                                     info={"images": {"1": {"url": "https://caa.example/1"},
                                                                      "k": {"file": picture}}}))
    question = _wait_for_question(front)
    urls = question["images"]
    assert set(urls) == {"1", "k"}
    assert front.image(urls["k"].rsplit("/", 1)[1]) == (picture.read_bytes(), "image/jpeg")
    first = urls["1"].rsplit("/", 1)[1]
    assert front.image(first)[1] == "image/png" and front.image(first)[1] == "image/png"
    assert fetched == ["https://caa.example/1"]                         # downloaded once
    with pytest.raises(web.ApiError) as unknown:
        front.image("made-up")
    assert unknown.value.status == 404
    front.answer(question["id"], "k")
    thread.join(2)


def test_the_tag_editor_question_follows_a_renamed_folder(tmp_path):
    front = web.WebFront()
    thread, result = _in_thread(lambda: front.edit_tags(tmp_path / "Old"))
    question = _wait_for_question(front)
    assert question["kind"] == "tag_editor" and front.tag_folder() == tmp_path / "Old"
    front.moved_tags(tmp_path / "New")
    front.answer(question["id"], None)
    thread.join(2)
    assert result == [(tmp_path / "New", "done")]


def test_on_the_page_the_tags_step_offers_its_own_editor_first(tmp_path, monkeypatch, front):
    class Page(RecordingFront):
        def supports(self, feature):
            return feature == "tag_editor"

        def edit_tags(self, folder, moved=None):
            self.edited = folder
            return folder.parent / "Renamed", "done"

    folder = tmp_path / "Album"
    folder.mkdir()
    make_flac(folder / "01.flac", artist="IU", title="Blueming")
    renamed = tmp_path / "Renamed"
    page = Page(["w"])                                                  # the editor's Continue ends the step
    ui.use(page)
    album_after = state.AlbumState(folder)
    folder.rename(renamed)                                              # what the editor's save does
    session = app.new_session(default_cfg(), app.Options(editor="skip"), tmp_path / "config.json")
    monkeypatch.setattr(tools, "available_editors", lambda cfg: [])
    assert app.tags_step(session, album_after)
    first = page.questions[0][2]
    assert first["default"] == "w" and first["options"][0] == ("w", "edit the tags here")
    assert album_after.folder == renamed and page.edited == folder


# --- the lyrics step's handoff to lyrembed's page -----------------------------------
def test_lyrembed_is_told_where_its_page_leads_back(tmp_path, monkeypatch):
    cfg = default_cfg()
    cmd = lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "interactive", return_url="http://127.0.0.1:41877/")
    assert cmd[2:] == ["--no-browser", "--return-url", "http://127.0.0.1:41877/", "--return-name", "cdimport"]
    assert lyrics.lyrembed_command("lyrembed", cfg, tmp_path, "auto", None, "http://127.0.0.1:1/")[2:] == ["--auto"]

    with_return = "  --no-browser\n  --return-url URL  a 'Done' button ..."
    monkeypatch.setattr(lyrics.proc, "capture", _answers({"--help": with_return}))
    assert lyrics.probe("lyrembed").return_url
    monkeypatch.setattr(lyrics.proc, "capture", _answers({"--help": "  --no-browser"}))
    assert not lyrics.probe("lyrembed").return_url


def test_the_page_follows_lyrembeds_address_once(monkeypatch):
    front = web.WebFront()
    front.message("tool_start", "lyrembed DIR", name="cyanrip")
    front.tool_output(b"lyrembed is running at http://127.0.0.1:1234/?token=abc\n")   # not a handoff tool
    front.message("tool_end", name="cyanrip", code=0)
    assert not any(e["kind"] == "handoff" for e in front.events)

    front.message("tool_start", "lyrembed DIR --no-browser", name="lyrembed", handoff=True)
    front.tool_output(b"lyrembed is running at http://127.0.0.1:43851/?token=P88t-T_x\nOpen that address\n"
                      b"lyrembed is running at http://127.0.0.1:9/?token=again\n")
    handoffs = [e for e in front.events if e["kind"] == "handoff"]
    assert [e["url"] for e in handoffs] == ["http://127.0.0.1:43851/?token=P88t-T_x"]
    assert front.away                                  # the tab is on lyrembed's page
    front.message("tool_end", name="lyrembed", code=0)
    assert front.away                                  # ... and only just came back
    monkeypatch.setattr(web, "HANDOFF_GRACE", 0.0)
    assert not front.away


def test_on_the_page_lyrembeds_output_goes_to_the_page(tmp_path, monkeypatch, front):
    ran = []
    monkeypatch.setattr(lyrics.proc, "run", lambda cmd: ran.append(("run", cmd)) or 0)
    monkeypatch.setattr(lyrics.proc, "run_tail", lambda cmd: ran.append(("run_tail", cmd)) or (0, []))
    ui.use(None)
    lyrics.run_lyrembed("lyrembed", default_cfg(), tmp_path, "auto")
    front()
    lyrics.run_lyrembed("lyrembed", default_cfg(), tmp_path, "auto")
    assert [kind for kind, _ in ran] == ["run", "run_tail"]


def test_the_lyrics_step_hands_over_when_both_can(tmp_path, monkeypatch, capsys, album_dir):
    session = app.new_session(default_cfg(), app.Options(lyrics="interactive"), tmp_path / "config.json")
    session.lyrembed, session.lyrembed_page, session.lyrembed_return = "lyrembed", True, True
    session.page_url = "http://127.0.0.1:41877/"
    passed = []
    monkeypatch.setattr(app.lyrics, "run_lyrembed",
                        lambda lyr, cfg, folder, mode, romanize=None, return_url='': passed.append(return_url) or 0)
    album = state.load(album_dir)
    album.mark("tags")
    assert app.lyrics_step(session, album) and passed == ["http://127.0.0.1:41877/"]
    assert "Done – back to cdimport" in capsys.readouterr().out
    session.page_url = ""                              # in the terminal: nothing to come back to
    album.done.remove("lyrics")
    assert app.lyrics_step(session, album) and passed[-1] == ""


def test_the_page_leads_back_only_to_a_local_page(capsys):
    assert cli.local_page("http://127.0.0.1:41000/") and not cli.local_page("http://evil.example:80/")
    for argv in (["--return-url", "http://127.0.0.1:1/"], ["--web", "--return-url", "https://127.0.0.1:1/"],
                 ["--web", "--return-name", "lyrembed"]):
        with pytest.raises(SystemExit):
            cli.main(argv)


def test_the_page_knows_where_done_goes(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "run", lambda session: 0)
    run = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"),
                  back=("http://127.0.0.1:41000/", "lyrembed"))
    server = web._Server(run, "test-token")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        state = _http(server, "GET", "/api/state?since=0")[1]
        assert (state["returnUrl"], state["returnName"]) == ("http://127.0.0.1:41000/", "lyrembed")
    finally:
        run.thread.join(2)
        ui.use(None)
        server.shutdown()
        server.server_close()


# --- the tag editor: every tag, numbers, covers -------------------------------------
def _with_more_tags(folder):
    import mutagen.flac
    from mutagen.flac import Picture

    for n, path in enumerate(sorted(folder.glob("*.flac")), 1):
        audio = mutagen.flac.FLAC(path)
        audio["musicbrainz_albumid"] = "album-id"
        audio["label"] = "Sample Label"
        audio["isrc"] = f"ISRC{n}"
        audio["discnumber"] = "1"
        if n == 1:
            audio["lyrics"] = "[00:01.00]la"
        picture = Picture()
        picture.type, picture.mime, picture.data = 3, "image/jpeg", b"\xff\xd8\xff\xe0 cover"
        audio.add_picture(picture)
        audio.save()


def test_tag_editor_lists_every_tag_and_the_cover(tmp_path):
    folder = _ripped_album(tmp_path, titles=("A", "B"))
    _with_more_tags(folder)
    album = tageditor.read_album(folder)
    assert album["other"] == {"LABEL": ["Sample Label"], "MUSICBRAINZ_ALBUMID": ["album-id"]}   # the same everywhere
    assert [t["other"] for t in album["tracks"]] == [{"ISRC": ["ISRC1"], "MUSICBRAINZ_TRACKID": ["id-1"]},
                                                     {"ISRC": ["ISRC2"], "MUSICBRAINZ_TRACKID": ["id-2"]}]
    assert [t["hasLyrics"] for t in album["tracks"]] == [True, False]                          # lyrics: not listed
    assert album["cover"] and album["tracks"][0]["disc"] == "1"
    assert tageditor.cover(folder, "01 - A.flac") == (b"\xff\xd8\xff\xe0 cover", "image/jpeg")
    with pytest.raises(tageditor.TagEditError):
        tageditor.cover(folder, "../elsewhere.flac")


def test_tag_editor_writes_numbers_and_other_tags(tmp_path):
    import mutagen.flac

    folder = _ripped_album(tmp_path, titles=("A", "B"))
    _with_more_tags(folder)
    album = tageditor.read_album(folder)
    album["other"]["LABEL"] = ["Other Label"]                       # the album's: every track
    album["tracks"][1]["other"]["ISRC"] = ["ISRC9"]                 # a track's: that one
    album["tracks"][0]["other"]["ISRC"] = []                        # emptied: removed
    album["tracks"][1]["disc"] = "2"
    tageditor.save(folder, album)
    first, second = (mutagen.flac.FLAC(folder / name) for name in ("01 - A.flac", "02 - B.flac"))
    assert first["label"] == second["label"] == ["Other Label"]
    assert "isrc" not in first and second["isrc"] == ["ISRC9"] and second["discnumber"] == ["2"]
    assert first["lyrics"] == ["[00:01.00]la"] and len(first.pictures) == 1                  # untouched
    album = tageditor.read_album(folder)
    album["tracks"][0]["other"]["NEW_TAG"] = ["x"]
    with pytest.raises(tageditor.TagEditError, match="no tag NEW_TAG"):
        tageditor.save(folder, album)


def test_the_editor_can_go_back_to_the_cover_step(tmp_path, monkeypatch, front):
    class Page(RecordingFront):
        def supports(self, feature):
            return feature == "tag_editor"

        def edit_tags(self, folder, moved=None):
            self.opened = getattr(self, "opened", 0) + 1
            return folder, "cover" if self.opened == 1 else "done"

    folder = tmp_path / "Album"
    folder.mkdir()
    make_flac(folder / "01.flac", artist="IU", title="Blueming")
    covers = []
    monkeypatch.setattr(app, "offer_cover", lambda f: covers.append(f))
    monkeypatch.setattr(tools, "available_editors", lambda cfg: [])
    page = Page(["w"])
    ui.use(page)
    session = app.new_session(default_cfg(), app.Options(editor="skip"), tmp_path / "config.json")
    assert app.tags_step(session, state.AlbumState(folder))
    assert covers == [folder] and page.opened == 2                  # cover step, then the editor again

    web_front = web.WebFront()
    thread, result = _in_thread(lambda: web_front.edit_tags(folder))
    web_front.answer(_wait_for_question(web_front)["id"], "cover")
    thread.join(2)
    assert result == [(folder, "cover")]


# --- fixes from the review of the page (0.6.2) ---------------------------------
def test_the_page_never_installs_or_builds_cyanrip(tmp_path, monkeypatch, front):
    monkeypatch.setattr(install, "choose_route", lambda *a, **k: install.Route("source", "build"))
    monkeypatch.setattr(install, "install_cyanrip", lambda *a: pytest.fail("installed from the page"))
    monkeypatch.setattr(install, "build_from_source", lambda *a: pytest.fail("built from the page"))
    monkeypatch.setattr(install, "remote_info", lambda: REMOTE)
    cfg = default_cfg()
    cfg["cyanrip"]["channel"] = "master"
    session = app.new_session(cfg, app.Options(), tmp_path / "config.json")
    recorder = front([])                                  # any question fails
    app.offer_cyanrip_install(session)
    session.cyanrip = "/usr/bin/cyanrip"
    app.offer_cyanrip_update(session, install.Installed("0.9.3.1", None))
    assert recorder.questions == []
    assert sum("--install-cyanrip in a terminal" in text for _, text, _ in recorder.messages) == 2


def test_old_lyrembed_on_the_page_is_not_offered_interactively(tmp_path, monkeypatch, album_dir, front):
    session = app.new_session(default_cfg(), app.Options(lyrics="interactive"), tmp_path / "config.json")
    session.lyrembed = "lyrembed"                         # before 0.8.0: terminal menus the page cannot answer
    modes = []
    monkeypatch.setattr(app.lyrics, "run_lyrembed",
                        lambda lyr, cfg, folder, mode, romanize=None, return_url='': modes.append(mode) or 0)
    album = state.load(album_dir)
    album.mark("tags")
    recorder = front(["a"])                               # only the mode; no lyrics found asks nothing more
    assert app.lyrics_step(session, album)
    assert modes == ["auto"] and len(recorder.questions) == 1
    assert [key for key, _ in recorder.questions[0][2]["options"]] == ["a", "s"]
    assert "lyrics" in state.load(album_dir).done


def test_tag_editor_puts_names_back_when_a_rename_fails(tmp_path, monkeypatch):
    import mutagen.flac

    folder = _ripped_album(tmp_path, titles=("A", "B"))
    album = tageditor.read_album(folder)
    album["tracks"][0]["title"], album["tracks"][1]["title"] = "A2", "B2"
    real_rename = Path.rename

    def rename(self, target):
        if Path(target).name == "02 - B2.flac":
            raise PermissionError(13, "Permission denied")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(tageditor.TagEditError, match="tags are saved.*keep their names"):
        tageditor.save(folder, album)
    assert sorted(p.name for p in folder.glob("*.flac")) == ["01 - A.flac", "02 - B.flac"]
    assert not list(folder.glob(".cdimport-rename-*"))
    assert mutagen.flac.FLAC(folder / "01 - A.flac")["title"] == ["A2"]


def test_tag_editor_refuses_a_name_too_long_before_writing(tmp_path):
    import mutagen.flac

    folder = _ripped_album(tmp_path, titles=("A", "B"))
    album = tageditor.read_album(folder)
    album["tracks"][0]["title"] = "가" * 90               # 270 bytes in UTF-8
    assert "too long" in tageditor.plan(folder, album)["problem"]
    with pytest.raises(tageditor.TagEditError, match="too long"):
        tageditor.save(folder, album)
    assert mutagen.flac.FLAC(folder / "01 - A.flac")["title"] == ["A"]


def test_a_later_run_without_an_album_keeps_the_finished_one(tmp_path, monkeypatch, album_dir):
    album = state.load(album_dir)
    found = [album, None]
    for name in ("ask_missing_folders", "check_tools", "offer_staging_cleanup", "offer_resume"):
        monkeypatch.setattr(app, name, lambda session: None)
    monkeypatch.setattr(app, "disc_and_rip", lambda session: found.pop(0))
    monkeypatch.setattr(app, "continue_album", lambda session, album: None)
    monkeypatch.setattr(app, "summary", lambda session, album: None)
    monkeypatch.setattr(app.ui, "confirm", lambda prompt, default=True: True)   # start another run
    session = app.new_session(default_cfg(), app.Options(), tmp_path / "config.json")
    assert app.run(session) == 0
    assert session.album is None and session.last_album is album   # what serve's finished line names


# --- fixes from the second outside review (0.6.3) ------------------------------
def test_without_musicbrainz_the_disc_keeps_its_tracks_and_link(tmp_path, monkeypatch, capsys):
    session, previews, ripped = unknown_disc_session(tmp_path, monkeypatch)

    def preview(cyanrip, cfg, **kwargs):
        previews.append(kwargs)
        if kwargs["no_musicbrainz"]:                      # -N: no link, so no track range either
            return rip.parse_preview(0, ["Disc tracks: 6"])
        return rip.parse_preview(1, UNKNOWN_DISC)
    monkeypatch.setattr(app.rip, "preview", preview)
    # No MusicBrainz, then type the tags: six titles without "Number of tracks"; rip.
    answers = feed_input(monkeypatch, ["n", "t", "Album", "X", "", "1", "2", "3", "4", "5", "6", "r"])
    assert app.preview_and_rip(session, "/dev/sr0") is not None and answers == []
    assert ripped[0]["no_musicbrainz"] and len(ripped[0]["tags"].titles) == 6
    assert f"To add this disc to MusicBrainz later: {UNKNOWN_DISC[2]}" in capsys.readouterr().out


def test_a_merge_with_something_left_in_the_working_folder_still_counts(tmp_path, monkeypatch, capsys):
    library_folder = disc_folder(tmp_path / "lib" / "BTS" / "Young Forever", 1, 2)
    second = disc_folder(tmp_path / "work" / "Young Forever", 2, 2)
    (second / "Young Forever CD2.log").write_text("log 2")
    state.AlbumState(second, done=["rip", "tags", "lyrics"]).save()
    real_merge = library.merge_disc

    def merge_disc(plan, target):
        real_merge(plan, target)
        (second / ".directory").write_text("[Dolphin]")  # a file manager looked meanwhile
    monkeypatch.setattr(app.library, "merge_disc", merge_disc)
    session = app.new_session(dict(default_cfg(), library_dir=str(tmp_path / "lib")), app.Options(),
                              tmp_path / "config.json")
    session.work_dir, session.library_dir = tmp_path / "work", tmp_path / "lib"
    album = state.load(second)
    feed_input(monkeypatch, [""])                          # add the disc; nothing asked again
    assert app.finish_step(session, album) and album.folder == library_folder
    assert (library_folder / "2.01 - T1.flac").exists()
    assert (tmp_path / "work" / "logs" / "Young Forever CD2.log").exists()   # the log still left the library
    captured = capsys.readouterr()
    assert "stayed" in captured.out + captured.err and "Move failed" not in captured.out + captured.err


def test_disc_zero_and_names_ending_in_dots_and_spaces():
    assert library._disc({"discnumber": "0"}) == "0" and library._disc({"discnumber": "01/2"}) == "1"
    assert library._disc({}) == ""
    assert library.safe_component("Vol. 1 .") == "Vol. 1" and library.safe_component(" . ") == "_"


def test_the_page_stops_when_closed_before_the_flow_started(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "run", lambda session: pytest.fail("the flow started"))
    run = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"))
    assert run.prevent_start() and not run.started
    run.start()                                            # the page's first request, too late
    assert not run.thread.is_alive() and not run.prevent_start()
    started = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"))
    started._started.acquire()                             # as start() does
    assert not started.prevent_start() and started.started


def test_a_picture_file_is_read_when_its_question_comes(tmp_path):
    front = web.WebFront()
    preview = tmp_path / "cover-preview.png"
    preview.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    first = front._register_image({"file": preview})
    preview.write_bytes(b"\x89PNG\r\n\x1a\nsecond")       # a changed cover under the same name
    second = front._register_image({"file": preview})
    preview.unlink()                                        # deleted after the question
    assert first != second
    assert front.image(first) == (b"\x89PNG\r\n\x1a\nfirst", "image/png")
    assert front.image(second)[0].endswith(b"second")
    with pytest.raises(web.ApiError) as missing:
        front.image(front._register_image({"file": tmp_path / "gone.png"}))
    assert missing.value.status == 502


def test_the_page_shows_no_file_link_for_the_cover(tmp_path, monkeypatch, front):
    shown = tmp_path / "cover-preview.jpg"
    monkeypatch.setattr(covertool.cover, "preview", lambda flac: shown)
    recorder = front()
    assert covertool._show_preview([]) == shown
    assert not any("file://" in text for _, text, _ in recorder.messages)
    ui.use(None)
    assert covertool._show_preview([]) == shown            # the terminal links it


def test_the_flow_follows_a_folder_the_tag_editor_renamed(tmp_path):
    front = web.WebFront()
    followed = []
    def edit():
        try:
            front.edit_tags(tmp_path / "Old", moved=followed.append)
        except web.Cancelled:
            pass                                           # the stop below, as on Ctrl+C
    thread, _ = _in_thread(edit)
    _wait_for_question(front)
    front.moved_tags(tmp_path / "New")                     # a save renamed it; the editor is still open
    assert followed == [tmp_path / "New"]
    front.stop()                                           # stopped before Continue
    thread.join(2)
    front.moved_tags(tmp_path / "Newer")                   # no editor open: nobody to tell
    assert followed == [tmp_path / "New"]


def test_tag_editor_checks_the_cue_and_log_names_too(tmp_path):
    import mutagen.flac

    folder = _ripped_album(tmp_path, titles=("A", "B"))
    long_album = "x" * 238                                 # folder 248 bytes: fits
    (folder / "Night∶ Lines (secure re-rip).log").write_text("re-rip log")   # 238 + 20 bytes: does not
    album = tageditor.read_album(folder)
    album["album"] = long_album
    with pytest.raises(tageditor.TagEditError, match="too long"):
        tageditor.save(folder, album)
    assert mutagen.flac.FLAC(folder / "01 - A.flac")["album"] == ["Night: Lines"]   # nothing written


def test_tag_editor_allows_a_change_of_case_on_a_case_insensitive_disk(tmp_path, monkeypatch):
    folder = _ripped_album(tmp_path, titles=("opening", "B"), album="night lines")
    real_exists = Path.exists

    def exists(self):                                      # as exFAT or SMB answer
        if real_exists(self):
            return True
        parent = self.parent
        return real_exists(parent) and any(p.name.lower() == self.name.lower() for p in parent.iterdir())
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(tageditor, "_same", lambda a, b: a.parent == b.parent and a.name.lower() == b.name.lower())
    album = tageditor.read_album(folder)
    album["album"] = "Night Lines"
    album["tracks"][0]["title"] = "Opening"
    result = tageditor.save(folder, album)
    moved = tmp_path / "Night Lines (Limited)"
    assert result["folder"] == str(moved)
    assert sorted(p.name for p in moved.iterdir()) == ["01 - Opening.flac", "02 - B.flac",
                                                        "Night Lines.cue", "Night Lines.log"]


def test_version_suffixes_only_rank_pre_releases_lower():
    key = install.version_key
    assert key("0.9.3.1") < key("0.9.4-rc1") < key("0.9.4-rc3") < key("0.9.4")
    assert key("0.9.4-beta2") < key("0.9.4-rc1") and key("0.9.4-dev") < key("0.9.4-alpha")
    assert not key("0.9.3.1-1") < key("0.9.3.1") and not key("0.9.3.1+dfsg") < key("0.9.3.1")


def test_own_editors_are_checked_like_the_built_in_ones(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"editors": {
        "puddletag": {"name": "Puddletag", "commands": [["puddletag", "{dir}"]], "cmd": "typo"},
        "broken": "puddletag {dir}",
        "eartag": "not an object"}}), encoding="utf-8")
    with pytest.raises(config.ConfigError) as problems:
        config.load_config(path)
    text = str(problems.value)
    assert "unknown setting 'editors.puddletag.cmd'" in text
    assert "'editors.broken' must be an object" in text and "'editors.eartag' must be dict" in text


def test_config_warnings_are_repeated_on_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "run", lambda session: 0)
    run = web.Run(app.new_session(default_cfg(), app.Options(), tmp_path / "config.json"),
                  warnings=["Warning: 'lyrics_mode' must be one of ask, auto, interactive, skip"])
    try:
        run.start()
        run.thread.join(2)
        assert [(e["kind"], e["text"]) for e in run.front.events][0] == (
            "warn", "Warning: 'lyrics_mode' must be one of ask, auto, interactive, skip")
    finally:
        ui.use(None)


def test_musicbrainz_requests_keep_their_distance_across_threads(monkeypatch):
    clock = [100.0]
    slept = []
    monkeypatch.setattr(cover.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cover.time, "sleep", lambda s: slept.append(s) or clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(cover, "_last_mb_request", 0.0)
    threads = [threading.Thread(target=cover._wait_for_musicbrainz) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(2)
    assert slept == pytest.approx([cover.MB_INTERVAL_SECONDS] * 2)   # one after another

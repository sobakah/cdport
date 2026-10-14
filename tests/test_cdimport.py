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
    """proc.run stand-in that creates files relative to cwd, like cyanrip."""
    def run(cmd, cwd=None):
        for name in files:
            target = Path(cwd) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
        return code
    return run


def test_rip_moves_album_out_of_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run", fake_cyanrip(["Album/01 - A.flac", "Album/Album.log"]))
    folder, leftover = rip.rip("cyanrip", default_cfg(), tmp_path)
    assert folder == tmp_path / "Album"
    assert (folder / "01 - A.flac").exists() and (folder / "Album.log").exists()
    assert leftover is None
    assert not list(tmp_path.glob(STAGING_PREFIX + "*"))


def test_rip_never_overwrites_existing_album(tmp_path, monkeypatch):
    (tmp_path / "Album").mkdir()
    monkeypatch.setattr(rip.proc, "run", fake_cyanrip(["Album/01.flac"]))
    folder, _ = rip.rip("cyanrip", default_cfg(), tmp_path)
    assert folder.name == "Album (2)"


def test_rip_keeps_leftovers_and_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run", fake_cyanrip(["Artist/Album/01.flac", "stray.log"]))
    folder, leftover = rip.rip("cyanrip", default_cfg(), tmp_path)
    assert folder == tmp_path / "Album"
    assert leftover is not None and (leftover / "stray.log").exists()

    monkeypatch.setattr(rip.proc, "run", fake_cyanrip(["Other/01.flac"], code=1))
    with pytest.raises(rip.RipError, match="exit code 1"):
        rip.rip("cyanrip", default_cfg(), tmp_path)
    assert not (tmp_path / "Other").exists()  # partial rip stays in staging


def test_rip_without_audio_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run", fake_cyanrip(["Album/cover.jpg"]))
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
    monkeypatch.setattr(lyrics, "can_romanize", lambda lyrembed: False)
    monkeypatch.setattr(app, "offer_cover", lambda folder: None)
    monkeypatch.setattr(tools, "find_lyrembed", lambda cfg: lyrembed)
    monkeypatch.setattr(tools, "find_editor", lambda cfg, key: editor)
    monkeypatch.setattr(tools, "available_editors", lambda cfg: [editor] if editor else [])
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"work_dir": str(tmp_path / "work"), "library_dir": str(tmp_path / "lib")}),
                        encoding="utf-8")
    return cli.main(["--config", str(cfg_path), "--color", "never"] + argv)


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

    def fake_run(lyrembed, cfg, folder, mode, romanize=None):
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
    def interrupted(lyrembed, cfg, folder, mode, romanize=None):
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
    launched_with = []

    def fake_launch(cmd, settle_seconds=2.0):
        launched_with.append(cmd)
        return SimpleNamespace(failure=lambda: None)
    monkeypatch.setattr(app.tags.proc, "launch", fake_launch)
    # Tags: open (default), Enter when done, continue, refuse "continue anyway",
    # continue again and accept; finish: keep.
    feed_input(monkeypatch, ["", "", "c", "n", "c", "y", "k", ""])
    code = run_cli(tmp_path, monkeypatch, [str(album_dir), "--lyrics", "skip"], editor=editor)
    assert code == 0
    assert launched_with == [["eartag", str(album_dir / "01.flac"), str(album_dir / "02.flac")]]


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


def test_failed_measurement_offers_the_choice_again(tmp_path, monkeypatch):
    commands = []

    def run(cmd, cwd=None):
        commands.append(cmd)
        return 1  # "No track had AccuRip entry, cannot find offset!"
    monkeypatch.setattr(app.proc, "run", run)
    feed_input(monkeypatch, ["f", "b"])
    assert not app.ensure_offset(offset_session(tmp_path), "/dev/sr0")
    assert commands == [["cyanrip", "-f", "-d", "/dev/sr0"]]


def test_measured_offset_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(app.proc, "run", lambda cmd, cwd=None: 0)
    session = offset_session(tmp_path)
    feed_input(monkeypatch, ["f", "x", "f", "-12"])  # a typo, then measure again
    assert app.ensure_offset(session, "")
    assert json.loads(session.config_path.read_text(encoding="utf-8")) == {"cyanrip": {"offset": -12}}


def test_rip_failing_before_any_file_removes_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(rip.proc, "run", lambda cmd, cwd=None: 1)
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
    replaced, log = rip.rerip("cyanrip", default_cfg(), album, work, [2], eject=True)
    assert replaced == [album / "2 - B.flac"] and (album / "2 - B.flac").read_text() == "secure2"
    assert (album / "1 - A.flac").read_text() == "fast1" and (album / "Album.cue").read_text() == "full cue"
    assert log == album / "Album (secure re-rip).log" and (album / "Album.log").read_text() == "first log"
    cmd = commands[0]
    assert cmd[cmd.index("-P") + 1] == "max" and cmd[cmd.index("-l") + 1] == "2" and "-Q" in cmd
    assert not list(work.glob(STAGING_PREFIX + "*"))  # nothing left behind
    rip.rerip("cyanrip", default_cfg(), album, work, [2])
    assert (album / "Album (secure re-rip) (2).log").exists()  # a second re-rip keeps the first log


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
        return [album / f"{n}.flac" for n in tracks], log
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
    staging = tmp_path / f"{STAGING_PREFIX}20261011-191037"
    (staging / "Album").mkdir(parents=True)
    (staging / "Album" / "01.flac").write_text("x")
    session = offset_session(tmp_path)
    session.work_dir = tmp_path
    with rip.hold_lock(staging):
        assert rip.in_use(staging)
        feed_input(monkeypatch, [])  # must not even ask
        app.offer_staging_cleanup(session)
        assert "leaving it alone" in capsys.readouterr().out
    assert not rip.lock_path(staging).exists() and rip.in_use(staging) is False
    feed_input(monkeypatch, ["n"])  # now it is a leftover: asked, and kept
    app.offer_staging_cleanup(session)
    assert staging.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the lock uses flock")
def test_lock_of_a_crashed_run_counts_as_free(tmp_path):
    staging = tmp_path / f"{STAGING_PREFIX}x"
    staging.mkdir()
    rip.lock_path(staging).write_text("12345\n")  # left behind, nobody holds it
    assert rip.in_use(staging) is False


def test_rip_holds_the_lock_while_cyanrip_runs(tmp_path, monkeypatch):
    seen = []

    def run(cmd, cwd=None):
        seen.append(rip.in_use(Path(cwd)))
        return fake_cyanrip(["Album/01.flac"])(cmd, cwd)
    monkeypatch.setattr(rip.proc, "run", run)
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
    monkeypatch.setattr(cover.tempfile, "gettempdir", lambda: str(tmp_path))
    feed_input(monkeypatch, [""])  # keep
    covertool.offer(folder)
    preview = tmp_path / "cdimport-cover-preview.jpg"
    assert preview.read_bytes() == JPEG
    assert f"Current cover: {preview.as_uri()}" in capsys.readouterr().out


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


def test_can_romanize_reads_lyrembeds_help(monkeypatch):
    def help_text(text):
        return lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0, text, "")
    monkeypatch.setattr(lyrics.proc, "capture", help_text("  --auto ...\n  --romanize  in --auto mode ..."))
    assert lyrics.can_romanize("lyrembed")
    monkeypatch.setattr(lyrics.proc, "capture", help_text("  --auto ...\n  --dry-run ..."))  # lyrembed 1.0.0
    assert not lyrics.can_romanize("lyrembed")


@pytest.mark.parametrize("answer, expected", [("", None), ("y", True), ("n", False)])
def test_automatic_lyrics_asks_about_romanization(tmp_path, monkeypatch, album_dir, answer, expected):
    session = app.new_session(default_cfg(), app.Options(lyrics="auto"), tmp_path / "config.json")
    session.lyrembed, session.lyrembed_romanize = "lyrembed", True
    passed = []
    monkeypatch.setattr(app.lyrics, "run_lyrembed",
                        lambda lyr, cfg, folder, mode, romanize=None: passed.append(romanize) or lyrics.EXIT_OK)
    album = state.load(album_dir)
    album.mark("tags")
    feed_input(monkeypatch, [answer, ""])  # Enter = lyrembed's own setting; then continue
    assert app.lyrics_step(session, album)
    assert passed == [expected]


def test_old_lyrembed_is_not_asked_and_gets_no_flag(tmp_path, monkeypatch, album_dir):
    session = app.new_session(default_cfg(), app.Options(lyrics="auto"), tmp_path / "config.json")
    session.lyrembed = "lyrembed"  # lyrembed_romanize stays False
    passed = []
    monkeypatch.setattr(app.lyrics, "run_lyrembed",
                        lambda lyr, cfg, folder, mode, romanize=None: passed.append(romanize) or lyrics.EXIT_OK)
    album = state.load(album_dir)
    album.mark("tags")
    feed_input(monkeypatch, [""])  # lyrics embedded: continue
    assert app.lyrics_step(session, album) and passed == [None]


@pytest.mark.parametrize("batch_code, answers", [
    (lyrics.EXIT_OK, ["l"]),                      # all embedded; check them
    (lyrics.EXIT_BATCH_FAILURES, ["c", "l"]),     # some undecided; continue; check
])
def test_automatic_lyrics_can_be_checked_interactively(tmp_path, monkeypatch, album_dir, batch_code, answers):
    session = app.new_session(default_cfg(), app.Options(lyrics="auto"), tmp_path / "config.json")
    session.lyrembed = "lyrembed"
    codes = [batch_code, lyrics.EXIT_OK]
    modes = []
    monkeypatch.setattr(app.lyrics, "run_lyrembed",
                        lambda lyr, cfg, folder, mode, romanize=None: modes.append(mode) or codes.pop(0))
    album = state.load(album_dir)
    album.mark("tags")
    left = feed_input(monkeypatch, answers)
    assert app.lyrics_step(session, album)
    assert left == [] and modes == ["auto", "interactive"]
    assert "lyrics" in state.load(album_dir).done

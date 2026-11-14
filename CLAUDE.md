# cdport – notes for Claude Code

cdport turns an audio CD into a tagged, lyrics-embedded FLAC album by driving
existing tools in order: cyanrip rips, a tag editor (cdport's own on the page,
Ear Tag and co. in the terminal) revises the tags, lyrembed embeds lyrics. It
is glue: it never rips, tags from the network or fetches lyrics itself. Python
3.10+, mutagen, jeepney (D-Bus); one flow in `app.py` runs in the terminal or
on a page in the browser (`--web`, stdlib HTTP server, Preact/htm vendored).
User docs: `README.md`. There are no design docs; this file holds the design.

## The program family

<!-- This section is the same in all six repositories. Change it in all of them or none. -->

Six programs by the same owner, each in its own private repository `sobakah/<name>` with its own
CLAUDE.md. Other sessions work in the other repositories: change one only when the owner asks.

| Program | What it does | Works with |
|---|---|---|
| picktag | Tags and searches pictures, videos and documents (GTK 4, libadwaita) | reads keepbest's and media-optimizer's file journals; starts keepbest for *Find Duplicates* |
| keepbest | Finds duplicate pictures and videos and keeps the best copy (page in the browser) | hands its tab to media-optimizer; writes a file journal |
| media-optimizer | Converts pictures, GIFs and videos to smaller formats (page in the browser) | started from keepbest; writes a file journal |
| cdport | Rips CDs, tags them and adds covers and lyrics (page in the browser) | starts lyrembed; D-Bus service for Jewelcase |
| lyrembed | Finds lyrics, embeds them in music files, romanizes them (page in the browser) | started from cdport; D-Bus service for Jewelcase |
| Jewelcase | A GNOME music player for an album library, with Spotify (GTK 4, libadwaita) | uses cdport and lyrembed; never writes music files |

The file journal format is specified in picktag's `docs/JOURNAL_SPEC.md`. The programs with a page
share its look (libadwaita's design language): `static/base.css` and `static/icons.js` are
lyrembed's, copied unchanged into cdport, keepbest and media-optimizer; change them in lyrembed and
copy them back. Setup details of the owner's machine are in `CLAUDE.local.md` (not in git).

On image-based systems (Fedora Silverblue, Bluefin) the programs run on the host where the image
has what they need (pipx from its zipapp, or layered with `rpm-ostree`); cdport runs in a toolbox,
where cyanrip can be built, and Jewelcase comes as a Flatpak bundle on its releases. In a toolbox
or distrobox, what the host starts (app-menu entry, D-Bus service) enters the container, and what
a program opens in other apps (browser, Flatpaks, file manager) leaves it: `container.py`,
lyrembed's, copied unchanged into cdport, keepbest and media-optimizer; picktag's
`core/desktop.py`. The suites of the four make no container unless a test asks (an autouse
fixture), so they run alike in a toolbox. Each README has a section *Fedora Silverblue, Bluefin
and other image-based systems* under its installation.

### Working rules

- **Commits**: commit finished work once its tests pass, locally, on the default branch;
  experiments stay uncommitted. **Push, tags and releases only when the owner says so**; a request
  to release includes commit, tag and push. Pushed history is never rewritten; a mistake in a
  release is fixed with a new version.
- **Commit messages**: English; a subject of at most 72 characters saying what changed; a body
  wrapped at 72 saying why and what was verified. Written directly, no draft files.
- **Before pushing**, check the diff for personal data: the owner's name, home paths, file and
  folder names or contents from the owner's libraries, details of the owner's machine. Those
  belong in `CLAUDE.local.md`.
- **Verify, don't assert.** Run the code before saying something works; say what was tested and
  what wasn't. Outside reviews have been wrong before: check each claim.
- **Every bug fix comes with a test that fails without it.**
- **Tests never touch the owner's data** (libraries, music, settings, journals, the trash), the
  network or real hardware; they use temporary folders and generated media.
- **Ask first** before installing system packages, deleting or overwriting the owner's files, or
  changing a default the owner chose.
- Stop processes by PID, never with `pkill -f`. Commands for the owner to paste are written for
  fish; examples in the docs use bash.

### Versions and releases

- Versions have three parts and tags no `v` (`1.2.0`). New features raise the minor version. A
  release of a few changes that fix bugs is a patch, also when a fix changes behaviour. The
  version lives in one place (see Releasing below).
- Every user-visible change goes under `## [Unreleased]` at the top of `CHANGELOG.md` when it is
  committed. Don't propose a release after every change: say what is waiting and suggest one when
  a feature or a few fixes have gathered.
- A release: the version raised, `## [Unreleased]` becomes `## [X.Y.Z] — YYYY-MM-DD`, the full test
  suite passes, one commit `<program> X.Y.Z: <what it brings>`, an annotated tag
  `git tag -a X.Y.Z -m "<program> X.Y.Z"`, push of the branch and the tag, then a GitHub release
  titled `<program> X.Y.Z` whose notes are that version's CHANGELOG section.
- Installed copies look for newer releases by their tags, so a tag above the current version must
  never exist. Pre-releases below 1.0.0 on GitHub are an archive; they are not mentioned in the
  repository.

### Documentation

- `README.md` is for people using the program: what it does, installing, using it, settings,
  working with the other programs, limits. Current state only.
- `CHANGELOG.md` follows Keep a Changelog, newest first: `### Added`, `### Changed`, `### Fixed`,
  bullets in the user's terms (a bold lead-in for the important ones), no internal reasons, no
  commit hashes.
- `CLAUDE.md` is for Claude sessions: the same section order in every repository. Keep a reason
  only where it stops a session from undoing a decision.
- Update README and CHANGELOG in the commit that changes the behaviour.

### Wording

British spelling (colour, behaviour, analyse); names of code, GTK and GNOME stay as they are.
Program names are written as above (lower case, except Jewelcase). Terms used in all six:

| Use | Not |
|---|---|
| folder | directory (except in code and option names) |
| the page (the program's page in the browser) | web UI, frontend, site |
| settings | options, preferences (except GNOME's *Preferences* window in the GTK apps) |
| dry run (`--dry-run`) | simulation, test run |
| the trash; *move to the trash* | recycle bin, wastebin |
| the owner (in CLAUDE.md) | the user, the maintainer, a name |
| you (in README) | the user |
| Enter, Esc, Ctrl+C | ENTER, Return, ^C |

## Layout

```
cdport
 ├─ 0. Tools      cyanrip, a tag editor, lyrembed: find them, offer to install
 ├─ Start         a disc, an unfinished album, any album folder, or only its tags
 ├─ 1. Disc       audio CD in the drive? preview the MusicBrainz match
 ├─ 2. Rip        cyanrip → FLAC with tags, cover, AccurateRip log
 ├─ Cover         offered whenever an album is opened
 ├─ 3. Tags       cdport's editor (page) or Ear Tag/Kid3/Picard
 ├─ 4. Lyrics     lyrembed, automatic or interactive
 └─ 5. Finish     offer to move the album into the music library, summary,
                  then back to Start
```

- `cdport/` package. `app.py` runs the flow; one module per step: `disc`,
  `rip`, `tags`, `lyrics`, `library`. Around them: `state` (the per-album
  state file), `tools` (finding programs), `proc` (subprocesses, pty,
  interrupts), `config`, `ui` (every message and question), `cli`,
  `install` (cyanrip: packages, source build, update check), `selfupdate`
  (cdport's own updates), `cover` (Cover Art Archive/MusicBrainz, no UI),
  `musicbrainz` (release search for unknown discs, no UI), `covertool` (the
  cover step and `cdport-cover`), `desktop` (app-menu entry), `web` (the
  page), `tageditor` (the page's editor, no UI), `audiotags` (FLAC, MP3,
  Ogg, Opus and M4A tags under one set of names, no UI), `dbus_service`,
  `container` (toolbox and distrobox, lyrembed's, unchanged; see the
  family block).
- `cdport/static/`: the page. `base.css` and `icons.js` are lyrembed's
  (see the family block), `style.css` holds the teal accent and what only
  this page has, `vendor/preact-htm.js` is the file lyrembed vendors (htm
  3.1.1's Preact bundle; checksum and licences in `vendor/LICENSES.txt`).
  `icon.svg` is the app icon, `logo.svg` the small one for the header and
  the browser tab (the full icon is unreadable below about 64 px).
- `run.py` and `cover.py`: launchers for a checkout (`run.py`, not
  `cdport.py`, which would shadow the package).
- `tests/test_cdport.py`, all tests in one file; `tests/data/`: 50 ms of
  silence as MP3, Ogg, Opus and M4A, made once with FFmpeg (tests never
  run it).
- `config.example.json` is generated from `DEFAULT_CONFIG` and
  `SETTING_HELP` in `config.py` (see Conventions).

## Commands

```bash
pip install -e ".[dev]"          # development install (pytest, pyflakes)
./run.py                         # run from the checkout; ./cover.py for cdport-cover
python3 -m pytest -q             # the whole suite, about 10 s
pyflakes cdport tests
python -c 'from pathlib import Path; from cdport import config; config.write_example(Path("config.example.json"))'
```

The tests need mutagen and jeepney. Without a development venv, put the
pipx venv's packages on the path:
`PYTHONPATH=~/.local/share/pipx/venvs/cdport/lib/python3.*/site-packages python3 -m pytest -q`
(the checkout's `cdport/` comes first, as `-m` puts the current folder in
front).

## Rules that must not break

- **Separate programs that hand off to each other.** cdport starts cyanrip,
  the editor and lyrembed as subprocesses and never imports lyrembed (that
  keeps it working with pipx installs and keeps the tools independent).
- **Linux first**, but nothing that rules Windows out: no shell-specific
  code, no hard-coded `/dev` paths outside the Linux backend.
- **Everything in English**: code, docs, UI text.
- **Never install silently**: show the exact command, ask, use `sudo` only
  for system package managers. A missing optional tool disables exactly its
  step and says so there.
- **Working folder and music library have no defaults.** On first use
  cdport asks for both, prefilled with `~/Temp` and the desktop's music
  folder (`XDG_MUSIC_DIR`), and saves `work_dir`/`library_dir`; `-w`/`-l`
  override them for one run. A wrong value in the config falls back to that
  setting's default and is reported; the rest still applies.
- **Never overwrite an album in the library.** Rips land in the working
  folder; at the end cdport *offers* the move. Exception, merging discs:
  cyanrip's `-D` scheme has no disc number, so disc 2 finds disc 1's library
  folder. `library.plan_disc_merge` offers to add the disc when that folder
  holds the same release (`musicbrainz_albumid`, else album + album artist)
  with disjoint `discnumber`s. cyanrip names the files apart (`1.01 - …`,
  `2.1 - …`; `<album> CD2.cue/.log`); byte-identical files (`cover.jpg`) are
  dropped, anything else of the same name stops it; a merge that fails
  halfway moves the files back.
- **Rip logs stay out of the library**: after the move, `*.log` goes to
  `<work_dir>/logs/` (`library.keep_logs_out`, "name (2).log" instead of
  overwriting). The `.cue` stays with the album.
- **A running rip is locked** (`<staging>.lock`, flock), so another cdport
  never offers to delete it as an interrupted rip. The lock is taken before
  the folder exists (`rip.staging_dir`); `rip.in_use` opens it read-only, so
  checking never creates a lock file.
- **A drive in use is locked** (`disc.hold`, flock on
  `<runtime_dir>/drive-<sr0>.lock`, from the disc preview to the end of the
  rip). Another cdport skips that drive, also in the ioctl fallback of
  detection. Looking at a free drive does not take the lock.
- **Every message and question goes through `ui`**, never `print()` or
  `input()` elsewhere; options and defaults are data, not text. That is
  what lets the page run the same flow (see Conventions).
- **cdport's own updates install only in step 0 and at the end of a run**,
  never whenever the page likes (as lyrembed does): pipx replaces files a
  running rip still imports from. Never when started with `--return-url`
  (lyrembed's Import a CD runs the show then).
- **The lines other programs read keep their wording**: `web.RUNNING_LINE`
  ("{name} is running at {url}") and `web.FINISHED_LINE` ("{name} finished:
  {folder}"), read by lyrembed, Jewelcase and `dbus_service`. `{name}` is
  `config.program_name()`. The finished line names the newest album that got
  through its steps (`last_album`): a stopped one is not finished, and
  lyrembed would announce its working folder as imported.
- **The old name, cdimport, stays where other programs or old data depend
  on it** (the owner's decision; remove only when asked):
  - commands `cdimport` and `cdimport-cover` as aliases in `pyproject.toml`;
    started as `cdimport`, the running/finished lines say cdimport;
  - `config.move_old_config` moves `~/.config/cdimport` to
    `~/.config/cdport` at the first start and leaves a symlink (Jewelcase
    may read the config there). Until moved, `config_dir` keeps using the
    old folder, so a failed move loses nothing; a `~/.config/cdimport` that
    is itself a symlink is not moved and stays in use. The message also goes
    to the page (`web.serve`'s `notes`/`warnings`);
  - hidden names `.cdimport.json` (the state file, which Jewelcase looks
    for) and `.cdimport-rip-` (staging folders), and the runtime folder
    `$XDG_RUNTIME_DIR/cdimport` (`config.runtime_dir`), so old and new
    installs see each other's drive locks;
  - `desktop.old_entry` replaces the app-menu entry cdimport wrote (only one
    with `Name=cdimport`); `cli.OLD_RESTART_ENV` takes the page address from
    a cdimport that updated itself on the page.
- **The D-Bus interface is no way out of a sandbox**: no call runs a command
  or works on a folder the caller names freely (see D-Bus under
  Conventions). `INTERFACE_VERSION` stays 1; a change that breaks callers
  gets a `cdport2` interface next to `cdport1`.
- **Page security as in lyrembed**: 127.0.0.1 only, the token as an
  HttpOnly SameSite cookie whose name carries the port (`web.cookie_name`;
  cookies belong to a host, and two pages on 127.0.0.1 overwrote each
  other's), `Host` and `Origin` checks, JSON only, a CSP. `/api/image/<id>`
  serves only sources the flow registered (JPEG and PNG), so the page cannot
  make cdport fetch arbitrary addresses. `--return-url` takes local
  addresses only (`cli.local_page`).
- **The tag editor writes safely**: everything is checked before anything is
  written (`tageditor._fits_formats`, name lengths, writability); renames go
  through temporary names (two tracks may swap titles) and come first, tags
  last, and names are put back on failure. It never adds new keys; lyrics
  tags are lyrembed's and only noted; the rip log's content is never changed
  (its checksum).
- **MusicBrainz**: at most one request per second (across threads), a 503
  retried twice, User-Agent `cdport/<version>` without contact details
  (never add the owner's email without asking).

## Conventions

- **Errors are reported, never swallowed.** No bare `except: pass`.
- **Optional dependencies stay optional**: a missing package disables one
  feature and says so where it is used (jeepney is imported only for
  `--dbus` and the reload).
- **Type hints and docstrings** on public functions; docstrings say *why*.
- **Code shared between the family's programs is copied, not imported**
  (`selfupdate.py` from picktag's `core/selfupdate.py`, `desktop.py` and the
  page's security from lyrembed).
- **Settings**: a new or changed setting goes into `DEFAULT_CONFIG` and
  `SETTING_HELP` in `config.py`, then `config.example.json` is regenerated;
  `test_example_config_is_current` fails until then. Config keys:
  `work_dir`, `library_dir`, `library_layout`, `lyrics_mode`, `editor`,
  `editors`, `cyanrip.{channel, path, device, offset, mode, deemphasis,
  replaygain, eject, dir_scheme, extra_args}`, `lyrembed.{path, extra_args}`,
  `cyanrip_source_dir`, `cyanrip_build_dir`, `color`, `check_for_updates`.
- **Several artists follow Picard**: `ARTIST` is the credit, `ARTISTS` the
  names; the same for `ALBUMARTIST`/`ALBUMARTISTS`. cyanrip writes the
  credit only.

### Front ends

`ui.use(front)` hands every `ui` call (`info`, `warn`, `check`, `choose`,
`confirm`, `ask`, `ask_path`, `wait_for_enter`, ...) to a `ui.Front` with its
plain content; without one it is the terminal. An external tool's output goes
through `ui.tool_output` (`proc.run_tail`). A front end that wants live tool
progress keeps the pseudo-terminal (`Front.wants_pty`): in a pipe, cyanrip
buffers its progress display.

The page (`web.py` + `static/`): `web.Run` runs `cli.run_session` in a thread
with `WebFront`.
- `WebFront` keeps messages and tool output as events with revision numbers;
  the page long-polls `/api/state?since=N`, and a question blocks the flow
  until `/api/answer`, checked against the open question. A line a progress
  display redraws with `\r` counts only in its newest state (`partial`).
- The flow starts on the page's first visit (`Run.start`), not at launch.
  A page closed before its first request ends the run.
- Stopping (`WebFront.stop`) is final: the open question, every later one and
  a pause (`ui.pause`, the disc wait) raise `Cancelled` (a
  `KeyboardInterrupt`), so the flow ends as on Ctrl+C (130). While a tool
  runs, `proc.interrupt_tool` sends it SIGINT and cyanrip finishes its log. A
  page that is gone does not stop a running tool. Ctrl+C in `serve`'s
  terminal already reached the tool (same process group,
  `proc.terminal_interrupts_tools`), so it gets no second SIGINT.
- Installing cyanrip works on the page: the source build runs with
  `proc.run_shown`; a package manager's command (sudo password, its own yes)
  is shown for a terminal by `install.run_package_manager`, which asks "Has
  it finished?", and the caller checks what is installed.
- Pictures: `ui.choose(..., info={"images": {key: {"url"|"file": ...}}})`;
  the terminal ignores `info`. A file (the cover preview, rewritten under the
  same name) is read when registered, under a new id each time. Release
  thumbnails come from `cover.release_thumbnail`.
- The tag editor (`tageditor.py`): `ui.supports("tag_editor")` adds option
  "w" to the tags step as the default; `ui.edit_tags` returns the folder
  (renamed if the album changed) and what comes next, `"done"` or `"cover"`
  ("Change the cover…" saves, runs `offer_cover` and reopens the editor). It
  shows every tag: the fields it edits, and the rest under "More tags",
  album-wide when equal in every track, else per track. The embedded front
  cover (type 3, else the first) via `/api/tags/cover`. A save that renames
  the folder calls `edit_tags`'s `moved` at once, so the flow knows the
  folder even if stopped before Continue. Names follow the tags only where
  cyanrip built them (`{track} - {title}`, `{album}...`, its "unicode"
  replacements copied in `tageditor.cyanrip_name`); a track number change
  does not rename. The `.cue` FILE lines follow, `.cue`/`.log` follow the
  album.
- Formats (`audiotags`): FLAC, MP3, Ogg Vorbis, Opus and M4A
  (`audiotags.EXTENSIONS`, lyrembed's formats); WAV, AIFF, WavPack and TTA
  are only counted. `audiotags.TagFile` maps cdport's Vorbis names to ID3
  frames (Picard's: TPE2 album artist, `TXXX:ARTISTS`) and MP4 atoms
  (`aART`, `----:com.apple.iTunes:ARTISTS`, `trkn` pairs); other text tags
  appear under Picard's name, else the frame ID, a TXXX's description or the
  atom. Binary frames and atoms (UFID, PRIV, `cpil`) are not listed and
  stay. MP3s keep their ID3 version. An M4A cover has no type: its first is
  the front cover. ID3 tells pictures apart by description, so a new front
  cover takes one no other picture has.
- Going elsewhere: `ui.navigation(album, steps, now, targets)` offers
  targets; a front end answers the open question (or the disc wait's pause)
  with `ui.Jump`, only to an offered target. `continue_album` catches the
  album's targets (the step runs, done or not; Tags opens the editor
  directly), then `AlbumState.next_step(after=…)` goes on; `cover` returns
  to the step it interrupted; `start` goes up to `run`/`start`, leaving the
  album resumable. The disc step offers only `start`; step 0, the folder
  questions and the staging cleanup offer nothing, so a jump never lands
  where nobody catches it. `finish_step` marks the steps jumped past as
  skipped (`skip_open_steps`). On the page: `/api/goto` (`WebFront.jump`)
  refuses while a tool runs, while stopping, and for targets not offered;
  the album bar shows `/api/album-cover` (`tageditor.album_cover`); the tag
  editor reports unsaved changes (`onDirty`) and a jump asks first. The
  terminal has no jumps; `--step` is its equivalent.
- Editing only (`edit_tags_only`, `--edit-tags`, the start's `t`):
  `AlbumState.transient` never writes a state file; no tools step, no
  update check. The "finished" line names the folder afterwards (renamed
  when the album was), which D-Bus `Finished` passes on.
- `ui.ask_path(..., folders=True)` lets the page list folders
  (`/api/folders`, only while such a question is open; working folder,
  library, home).
- The page scrolls like a terminal: it follows new content only while the
  view is at the end, also inside a tool's output box. A ResizeObserver
  catches late growth, and the page's own `scrollTo` is flagged (`ownScroll`)
  so its scroll event does not count as the reader leaving the end.
- The header bar: program, workflow steps as icons in the middle (no
  numbers; the terminal shows them), actions. Editing only shows "Tag
  editor" instead of the steps.
- App menu (`desktop.py`): pipx cannot install desktop files, so
  `--install-desktop-entry` writes `~/.local/share/applications/cdport.desktop`
  and the icon, starting the cdport that wrote it with `--web`
  (`Terminal=false`).

### Lyrics and lyrembed

- Modes: automatic (`lyrembed DIR --auto`, with `--romanize`/`--no-romanize`
  or neither, so lyrembed's own `settings.auto_romanize` decides) or
  interactive (`lyrembed DIR`, a page in the browser). cdport starts lyrembed
  and waits for it to exit.
- On cdport's page the tab is handed over: cdport starts `lyrembed DIR
  --no-browser --return-url <its page> --return-name cdport` with
  `ui.tool_start(..., handoff=True)`; `WebFront` reads "lyrembed is running
  at <url>" (`_HANDOFF`), adds a `handoff` event, and the page follows it
  once (remembered in `sessionStorage`). lyrembed's "Done – back to cdport"
  ends lyrembed and `run_lyrembed` returns. Leaving sends cdport's own
  "closing", so the page does not count as gone while away or for
  `HANDOFF_GRACE` after (`WebFront.away`). On the page lyrembed's output
  (also `--auto`'s) goes through `run_tail`; in the terminal lyrembed gets
  the terminal (`proc.run`) and opens its own tab.
- Started by lyrembed's Import a CD (`--return-url URL --return-name
  lyrembed`, with `--web` only), the finished run's button leads back there,
  and lyrembed reads the finished line to open the album. lyrembed may be
  gone by then: `_say` survives the closed pipe, and lyrembed must not end
  cdport, which would cut a rip off.

### D-Bus

For Jewelcase, which may run as a Flatpak: the companion tools stay pipx
programs on the host, each owning a session-bus name the sandbox may call
(`--talk-name`), started by the bus through a service file. A sandbox can
neither start host programs nor read `~/.config/cdport` or the working
folder, so everything a caller needs has a call. The calls are in
`dbus_service`'s docstring and the README.
- Bus name `io.github.sobakah.cdport` (the app ID cdport would have as a
  Flatpak), object `/io/github/sobakah/cdport`, interface
  `io.github.sobakah.cdport1` (`GetInfo`'s `InterfaceVersion`).
- `Import` and `EditTags` start a page run as a child process
  (`dbus_service.page_command`: the same cdport, `--web --no-browser`, its
  own session) and answer once it prints its address (the reply waits, the
  loop does not; `URL_TIMEOUT` 20 s, below the 25 s D-Bus clients wait by
  default). Page runs get `--unvisited-timeout 600` (hidden option): a page
  nobody opened ends. Each run's output is read to the end in a thread;
  "… finished: FOLDER" becomes `Finished`'s album; the patterns are built
  from `web.RUNNING_LINE`/`web.FINISHED_LINE`.
- `Stop` sends SIGINT once per run (`PageRun.stopping`): a second one would
  interrupt `web.serve`'s own stopping and cut the rip off.
- The service never ends while runs are going (`_outlive_runs`): dbus-broker
  runs it as a systemd unit, and the unit's end kills its whole cgroup,
  cyanrip included. A call failing with anything but a `ServiceError` is
  answered with `org.freedesktop.DBus.Error.Failed`, traceback to stderr; it
  never ends the service. `IDLE_EXIT` (60 s) without calls or runs ends it,
  so an old version does not keep running after a pipx update.
- `Folder` (Import) must be one of `UnfinishedAlbums`; `Library` an existing
  absolute folder. `EditTags`' folder must hold files of
  `audiotags.EXTENSIONS` (`GetInfo`'s `TagFormats`) and resolve inside the
  config's library or working folder or the music folder (`editable_places`;
  not the home folder `music_dir_suggestion` may fall back to). Options are
  none yet; unknown ones are refused. `_busy_with` refuses a second run on
  the same album.
- `Service` holds the calls and runs without the bus (tests drive it with
  stand-in processes); `serve` is the jeepney loop: `receive`/`send` only.
- `--install-dbus-service` writes
  `~/.local/share/dbus-1/services/io.github.sobakah.cdport.service`
  (`Exec=` the launcher, as the desktop entry's, plus `--dbus`) and asks the
  bus to `ReloadConfig`.

## Testing

- **Tests never touch the network, the drive or the real tools.** The
  autouse fixture `no_real_tools` fails a test that starts cyanrip, eject,
  udevadm, lyrembed, flatpak, kid3, picard, git, a package manager, meson,
  ninja, pipx or pkg-config by name or outside the test's temp folders
  (`REAL_TOOLS`); test doubles are started by path. Fake `proc.run_tail` for
  cyanrip (faking `proc.run` once let the real cyanrip open the drive).
- `no_update_check` (autouse) makes `selfupdate.latest` fail; a test that
  needs an answer fakes it.
- `make_flac` writes a header-only FLAC mutagen can tag; the other formats
  come from `tests/data/`.
- `feed_input` answers prompts in order and fails on an unexpected one; the
  `front` fixture records a `ui.Front`'s questions and answers them.
- `test_dbus_on_a_real_bus_started_by_the_bus` starts a private dbus-daemon
  with the service file (skipped without jeepney or dbus-daemon). Its bus
  config needs session.conf's `<allow eavesdrop="true"/>`, else every reply
  is rejected and the client hangs; the bus and service get an empty `PATH`
  and a fake `HOME`, as they are outside `no_real_tools`' reach.
- The page was checked in headless Chrome; there is no browser test in the
  suite.

## Releasing

Follow "Versions and releases" in the family block. cdport's version is
`__version__` in `cdport/config.py` (pyproject reads it). `selfupdate.latest`
takes the highest plain `x.y.z` tag from `git ls-remote --tags`, so installed
copies see a release as soon as its tag is pushed.

## Gotchas

**cyanrip** (github.com/cyanreg/cyanrip; re-verify before relying on these)
- **The drive offset is mandatory**: without `-s` a rip exits 1 ("Offset is
  unset! To continue with an offset of 0, run with -s 0!"), although `-h`
  says "default: 0". `-I` works without it. cdport asks before the first rip
  and saves `cyanrip.offset`. `cyanrip -f` ends with `Drive offset of +N
  found (confidence: N)!` (tracks that confirmed it); cdport saves without
  asking from 4 (`app.MIN_OFFSET_CONFIDENCE`). It needs a disc in the
  AccurateRip database ("No track had AccuRip entry, cannot find offset!"
  otherwise, and no files written).
- **Disc not on MusicBrainz**: `-I` exits 1 with "Unable to find release
  info for this CD", prints the `https://musicbrainz.org/cdtoc/attach?toc=…`
  link (before the track list) and "To continue add metadata via -a or -t,
  or ignore via -N!". With `-R <release MBID>` it takes that release's tags
  anyway, but only from master bde6a1e (first in 0.9.4-rc3,
  `install.RELEASE_FOR_UNKNOWN_DISC`); older ones only pick among the disc
  ID's releases, so cdport hides `s`/`m` for such a disc. `-a`/`-t` are
  parsed with FFmpeg's `av_dict_parse_string` ("key=value:key=value",
  `-t N=title=...`) and cyanrip's `append_missing_keys` only understands
  backslash escapes, so `rip.tag_args` escapes `\ ' : =`. Keys: `album`,
  `album_artist`, `date`, `title`. `rip.parse_preview` reads link and track
  range from all of `-I`'s output.
- **Several releases for the disc ID**: `-I` (and a rip) exits 1 after
  "Multiple releases found in database for DiscID ...:", one line per
  release `    N (ID: <mbid>): Album (disambiguation) (country) (N CDs)
  (date)`, then "Please specify which release to use by adding the -R
  argument" (`src/musicbrainz.c`); `rip.parse_preview` reads them into
  `Preview.releases`.
- **Version**: releases up to 0.9.3.1 have no `-v` (`invalid option`, exit
  1); cdport reads `-h`'s first line: `cyanrip VERSION (TAG) help:` on
  releases, `cyanrip VERSION (COMMIT)` on master. v0.9.3.1's meson.build
  says 0.9.3, which Debian's and openSUSE's packages print (Ubuntu 24.04's
  0.9.2 says 0.9.0), so updates compare the first three numbers. Every line
  cdport parses (offset, unknown disc, several releases, AccurateRip, "Track
  N ripped and encoded successfully!") is the same across versions; "with
  errors." exists only in newer ones.
- **v0.9.3.1 does not compile against FFmpeg 9** (`AVCodec has no member
  ch_layouts` in `cyanrip_encode.c`); master builds. Hence `cyanrip.channel:
  "master"`. The repository's `nightly` tag is years old and GitHub has no
  nightly release, so the channel is called "master". Master built on
  Debian 13, Ubuntu 24.04, Arch and Tumbleweed; v0.9.3.1 on Debian and
  Ubuntu only.
- **Fast mode**: `-P none`, then `rip.accuraterip_results` reads each
  track's "(accurately ripped" from the log, and `rip.rerip` rips
  unconfirmed tracks with `-P max -l N,...`. "AccurateRip:" in `-I` must say
  "found", else the rip is secure from the start. A fast rip runs without
  `-Q`; `disc.eject` ejects afterwards (or the re-rip's `-Q` does). A single
  CDROMEJECT can fail with EBUSY (udev/udisks look at the drive after
  cyanrip closes it), so `disc.eject` retries 5× 1 s, then runs util-linux
  `eject` (SCSI fallback, as libcdio's `cdio_eject_media` does for `-Q`).
- **A partial rip (`-l`) measures only its own tracks as "the album"**
  (`crip_replaygain_meta_album`), so re-ripped tracks keep the fast rip's
  album gain tags (`rip._keep_album_gain`).
- **cyanrip's R128 gain tags are 10 dB too high.** It writes
  `R128_TRACK_GAIN`/`R128_ALBUM_GAIN` next to correct ReplayGain tags, but
  relative to −13 LUFS instead of −23 (`src/cyanrip_main.c`:
  `lrintf((REPLAYGAIN_REF_LOUDNESS - t->ebu_integrated + 5) * 256)`, which
  should be `- 5`: R128 gain = ReplayGain gain − 5 dB). Present since
  v0.9.2. Players that prefer R128 tags (GStreamer's `rgvolume`) play such
  files about 10 dB too loud. Workaround: `rip.drop_r128_gain` deletes both
  R128 tags from FLAC files that have `REPLAYGAIN_TRACK_GAIN`, in `rip.rip`
  before the album leaves staging and in `rip.rerip` before the secure
  tracks replace the fast ones; `ALBUM_GAIN_TAGS` carries no R128 tag.
  Deleted rather than rewritten: they say the same as the ReplayGain tags
  (FLAC's convention), and deleting stays right once cyanrip is fixed. A
  file without ReplayGain tags (`cyanrip.replaygain: false`) keeps them; an
  Opus rip via `extra_args` (`-o opus`) still gets wrong values. To clean
  older rips: delete both tags with mutagen only in FLAC files that also
  have `REPLAYGAIN_TRACK_GAIN`.
- **Defaults**: FLAC compression fixed at 11 (FFmpeg's level); ReplayGain
  tags on (`-K` off); output 16-bit for normal CDs. On discs flagged with
  preemphasis cyanrip applies deemphasis by default, which changes the
  audio; `-W` keeps it bit-exact, but players ignore the flag, so such rips
  sound too bright. cdport keeps cyanrip's default (`cyanrip.deemphasis:
  true`).
- Options cdport uses: `-I` disc info without ripping, `-R` release (index or
  ID), `-D`/`-F` folder and file naming (use `-D` to know the output folder),
  `-Q` eject, `-W`, `-K`, `-N` no MusicBrainz, `-d` device, `-s` offset, `-f`
  find offset, `-P` paranoia, `-l` tracks, `-a`/`-t` tags.
- cyanrip embeds only an image the Cover Art Archive marks as *front*; a
  release whose only image is untyped gets `Album Art: none`, although its
  release group may have a front cover from another edition. Hence the
  cover step (`cover.py`): both, or a file; the 1200 px version embedded as
  front cover (never replacing a picture), `cover.jpg` saved unless one
  exists; changing swaps only the front-cover picture (type 3).
- Packaging: no package for Fedora, RPM Fusion, Terra or Flathub (the repo
  has a Flatpak manifest, but the source build is the chosen route on
  Fedora). Debian/Ubuntu `apt`, Arch AUR only, openSUSE Tumbleweed
  `zypper`, Alpine/Void/FreeBSD/NixOS their package managers; Windows
  (later): the latest release exe from GitHub. Re-check with Repology
  (`https://repology.org/api/v1/project/cyanrip`) rather than trust this.
- Build dependencies: `meson ninja-build gcc ffmpeg-devel
  libcdio-paranoia-devel libmusicbrainz5-devel libcurl-devel`, optional
  `qrencode-devel libchromaprint-devel` (Fedora names; `ffmpeg-devel` is in
  RPM Fusion). cdport checks them with pkg-config against the versions in
  cyanrip's `src/meson.build` and installs only what is missing
  (`install.dependency_command`): `apt` with `-dev` names (also by
  `ID_LIKE`), `pacman -S --needed` (headers included), `zypper` with
  `pkgconfig(NAME) >= VERSION`, which makes Tumbleweed pick FFmpeg 9.
  Distros without a mapping get the list of pkg-config names.

**cyanrip's update check** (at most once a day, cached in `~/.cache/cdport/`)
- Self-built: version and commit from `cyanrip -h` against `git ls-remote`
  of GitHub (newest `vX.Y.Z` tag, `master` commit), so it also works for a
  cyanrip built by hand. A release installed with `channel: master` is
  offered the master build at every start. A distro package only gets a
  note when GitHub has a newer release. Version comparison ranks only
  pre-releases (`rc`, `beta`, ...) below a release; a packager's suffix
  (`-1`) counts as the release.

**cdport's own updates** (`selfupdate.py`)
- `git ls-remote --tags` of the private repository: git has the access
  `pipx install` has (gh as credential helper) and never prompts
  (`GIT_TERMINAL_PROMPT=0`, no askpass). Asked at every start in a thread
  (`app.UpdateCheck`); step 0 offers what an earlier check found
  (`selfupdate.known`, `<cache>/self-update.json`, written whole by
  `remember`) without waiting and always has a cdport line (newest, could
  not look, or still asking); `late_self_update` names a newer answer at the
  end of the run; `--update` waits.
- A pipx install (`pipx_metadata.json` in `sys.prefix`) is updated after a
  yes: `pipx upgrade`, or `pipx install --force` with the new tag when the
  install is pinned. A checkout gets `git pull`, anything else the command.
- Restart from step 0: `app.Restart` reaches `cli.main`, which `exec`s the
  command cdport was started with, without `--update`. On the page the flow
  thread hands it to `Run.restart`; `serve` ends the server and raises it
  again with "PORT:TOKEN" in `cli.RESTART_ENV`, which the new process takes
  (same address and cookie) and removes at once. The page reloads when
  `/api/state` answers with another `instance`. A tab closed meanwhile gets
  no restart (nobody would reach or end the new process). At the end of a
  run nothing restarts: the same arguments would begin a run nobody asked
  for.

**lyrembed**
- Exit codes: 0 done/quit, 1 batch failures (unreadable files or failed
  writes only), 2 unusable input, 130 Ctrl+C. **Batch mode exits 0 also when
  it embedded nothing** ("no confident match", incomplete tags and
  instrumentals count as skipped) and has no machine-readable summary, so
  cdport reads the tracks after `--auto` (`lyrics.without_lyrics`: Vorbis
  `LYRICS`/`UNSYNCEDLYRICS`/`SYNCEDLYRICS`, ID3 `USLT`/`SYLT`, MP4 `©lyr`,
  as lyrembed's `tagging.py`) and offers interactive mode for what is left.
- It tags only `.flac .mp3 .ogg .opus .m4a`; cdport skips the lyrics step
  for albums with none of them.
- A pipx venv built on a Python the distribution later removed fails with
  "bad interpreter". Step 0 therefore runs `lyrembed --version` /
  `cyanrip -h` and reports such a tool with the repair command (`pipx
  reinstall NAME`); a tool that cannot start returns 127 instead of raising.

**Ear Tag** (Flatpak `app.drey.EarTag`)
- Registers `HANDLES_OPEN`, so files can be passed on the command line. Its
  sandbox only has `xdg-download` and `xdg-music`, so cdport passes files
  with `flatpak run --file-forwarding app.drey.EarTag @@ FILES @@` (document
  portal).
- Single-instance GApplication: with a window already open, the launch
  returns at once. cdport does not wait for the process; it asks to press
  Enter when done, then re-reads the tags and warns about tracks without
  artist or title (lyrembed's search needs both).

**Disc detection (Linux)**
- udev: `ID_CDROM_MEDIA_TRACK_COUNT_AUDIO` in `udevadm info -q property -n
  /dev/srN`; without external commands, the `CDROM_DISC_STATUS` ioctl via
  `fcntl`. Windows (later): `GetDriveTypeW == DRIVE_CDROM` via `ctypes`;
  audio CDs show `.cda` files.

**MusicBrainz**
- Attaching a disc ID ("Add disc ID", edit type 55) has a 7-day voting
  period and is accepted on expiry. Steps:
  musicbrainz.org/doc/How_to_Add_Disc_IDs.
- `cdport-cover` without a release ID searches release groups by the album
  and album-artist tags (again without a trailing "(...)" when nothing is
  found). A 503 is usually temporary.

**D-Bus**
- jeepney's `send_and_get_reply` after start-up drops every message that is
  not its reply, so `serve` uses only `receive`/`send`. Even `RequestName`
  is waited for by hand (`_request_name`): started by the bus, the call that
  started cdport arrives as soon as the name is taken, before that reply. An
  activated service answers on `DBUS_STARTER_ADDRESS`.
- dbus-daemon watches the services folder; dbus-broker reads it only on
  `ReloadConfig`.

## Open items

- Report cyanrip's R128 bug upstream (github.com/cyanreg/cyanrip) or send
  the fix (`+ 5` → `- 5` on both lines): only with the owner's go-ahead.
- Activation by dbus-broker itself (it starts services as transient systemd
  units) has not been checked; a private dbus-daemon and `--dbus` started by
  hand on the session bus have.
- `cdport1` returns the page address with its token, which opens the
  page's whole API (lyrembed's service opens the browser itself instead).
  Jewelcase's code expects the address; worth changing in a `cdport2`.
- Ear Tag: whether it accepts a folder, and whether it can save through the
  document portal, is untested.
- When lyrembed gets "automatic, then prompt only for the tracks it could not
  decide", offer it as a third lyrics mode.
- Once lyrembed and Jewelcase no longer depend on the old name, the
  `cdimport` output lines, hidden file names and aliases can go (owner's
  call).
- Windows support (planned; nothing built for it beyond keeping it possible).

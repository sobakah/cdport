# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`cdimport` is an interactive terminal program that turns an audio CD into a
tagged, lyrics-embedded FLAC album by driving existing tools in sequence:

```
cdimport
 ├─ 0. Tools      cyanrip, a tag editor, lyrembed: find them, offer to install
 ├─ 1. Disc       audio CD in the drive? preview the MusicBrainz match
 ├─ 2. Rip        cyanrip → FLAC with tags, cover, AccurateRip log
 ├─ 3. Tags       Ear Tag (or another editor) for manual revision
 ├─ 4. Lyrics     lyrembed, automatic or interactive
 └─ 5. Finish     offer to move the album into the music library, summary
```

It replaces the maintainer's manual workflow: copy WAVs off the CD, convert
them with `~/Projekte/Git-Repos/wav-to-flac`, tag them in Ear Tag, then run
lyrembed. It is glue: it does not rip, tag or fetch lyrics itself.

**Status:** 0.1.0 code written 2026-10-11: steps 1–5 on Linux, and step 0
installs cyanrip (package manager, or source build on Fedora;
`install.py`). Not yet tested end to end against a real CD, and the source
build has not run for real (the dnf step needs the maintainer's sudo).
Editor and lyrembed are only detected, not installed. `cyanrip.channel`
("release" default, or "master") and the update check at start are
built (`install.update_offer`, cache `~/.cache/cdimport/cyanrip-update.json`).
0.2.0 (2026-10-11): asks for another run after the summary, links the
embedded cover and MusicBrainz search hits, offers an interactive lyrembed
check after automatic lyrics.
0.3.0 (2026-10-12): discs MusicBrainz does not know (release search, release
link, typed tags via -a/-t, disc ID submission link) and a choice among
several releases for one disc ID.
0.4.0 (2026-10-12): easier-to-read output (marks, aligned lists, framed tool
output, clickable links; `ui.check`, `ui.field`, `ui.gap`, `ui.tool_start`).
0.4.1 (2026-10-14): fast rips by default (`cyanrip.mode`, AccurateRip check,
secure re-rip of unconfirmed tracks) and the drive lock against parallel runs.
0.4.2 (2026-10-14): second disc of an album joins the first one's library
folder; eject after a fast rip retries and falls back to util-linux eject.
0.5.0 (2026-10-15): fixes from an outside review (Gemini), each
checked against the code first: lyrics left after `--auto`, one-time
offset, config falls back per setting, link punctuation, re-rip leftovers,
cover checks, `run_tail` without a terminal and after Ctrl+C, disc merge
over all tracks with rollback, Flatpak option values.
0.5.1 (2026-10-21): ready for lyrembed 0.8.0's browser page (version check,
texts, how to get back to cdimport).
0.6.0 (2026-10-21): the page (`--web`, preview) with pictures, the tag editor,
the lyrics handoff to lyrembed, and `--return-url` for lyrembed's Import a CD.
0.6.1 (2026-10-21): the tag editor shows every tag, disc/track numbers and the
cover (with a way back to the cover step); the page scrolls like a terminal.
0.6.2 (2026-10-25): fixes from a code review of the page: stopping (disc
wait, Ctrl+C during a rip), no install or lyrembed terminal menus on the page,
lyrembed's address split over two reads, tag-editor renames put back.
0.6.3 (2026-10-26): fixes from a second outside review (Gemini), each
checked first: page closed before its first request, merge leftovers, the
rip lock taken before the folder exists, the tag editor's folder followed
at each save, case-only renames, the track range kept without MusicBrainz.
0.6.4 (2026-10-27): lyrembed's page is recognised from `lyrembed --help`
(`--no-browser`), not its version number; ready for lyrembed 1.0.0.
1.0.0 (2026-10-27), the first stable release; the patch releases 0.4.1 to
0.6.4 were folded into their line's first release (CHANGELOG, GitHub) and
their tags removed. New in it: other Linux distributions, checked in containers:
the packaged cyanrip is found (`-h`, not `-v`), build dependencies via
apt/pacman/zypper, the editor falls back to an installed one, no release
search for unknown discs with cyanrip before 0.9.4-rc3; and cdimport's own
update check (`selfupdate.py`, taken from picktag's `core/selfupdate.py`).

Layout: `cdimport/` package (`app.py` runs the steps, one module per step:
`disc`, `rip`, `tags`, `lyrics`, `library`; `state`, `tools`, `proc`,
`config`, `ui`, `cli`, `install`, `cover` (Cover Art Archive/MusicBrainz,
no UI), `musicbrainz` (release search for discs MusicBrainz does not know,
no UI) and `covertool` (the cover step and the `cdimport-cover` command)),
`run.py` and `cover.py` launchers, `tests/test_cdimport.py`.
`config.example.json` is generated from `DEFAULT_CONFIG` and `SETTING_HELP`
in `config.py`. After adding or changing a setting, update both and
regenerate it (see `config.write_example`); a test fails until then.

## Decisions made by the maintainer

- **Separate programs that hand off to each other**, not one program that
  does everything. cdimport calls `cyanrip`, the editor and `lyrembed` as
  subprocesses; it never imports lyrembed (that keeps it working with a pipx
  install and keeps the tools independent).
- **Linux first.** Windows is an afterthought, but do not make choices that
  rule it out (no fish/bash, no hard-coded `/dev` paths outside the Linux
  backend).
- **Python**, like lyrembed (Python 3.10+).
- **Everything in English**: code, docs, UI text.
- **Working folder** and **music library** have no defaults (decided
  2026-10-11): on first use cdimport asks for both, prefilled with `~/Temp`
  and the desktop's music folder (`XDG_MUSIC_DIR`, `~/Musik` for the
  maintainer), and saves them as `work_dir`/`library_dir` in the config.
  `-w`/`-l` override them for one run. A wrong value in the config file
  falls back to that setting's default and is reported; the rest still
  applies (2026-10-15: before, one typo dropped the folders and the offset). Rips land in the working folder
  first. At the end, *offer* to move the album to the library; never
  overwrite an existing album. **Exception, merging discs** (2026-10-14):
  cyanrip's `-D` scheme has no disc number, so disc 2 of an album finds
  disc 1's library folder (seen with "LOVE YOURSELF 結 'Answer'", "화양연화
  Young Forever"). `library.plan_disc_merge` offers to add the disc when
  that folder holds the same release (`musicbrainz_albumid`, else album +
  album artist) with disjoint `discnumber`s. Files are named apart by
  cyanrip (`1.01 - …`/`2.1 - …`; the track number is padded to each disc's
  own track count, `<album> CD2.cue/.log`); byte-identical files
  (`cover.jpg`) are dropped, anything else of the same name stops it.
- **Rip logs stay out of the library** (maintainer's request, 2026-10-11):
  after the move, `*.log` goes to `<work_dir>/logs/` (`library.keep_logs_out`,
  numbered "name (2).log" instead of overwriting). The `.cue` stays with
  the album.
- **A running rip is locked** (`<staging>.lock`, flock): another cdimport
  must not offer to delete it as an interrupted rip. Found 2026-10-11 when
  a test run did exactly that while the maintainer was ripping. The lock is taken
  before the folder exists (`rip.staging_dir`), and `rip.in_use` opens it
  read-only, so checking never creates a lock file again (2026-10-26).
- **A drive in use is locked too** (`disc.hold`, flock on
  `$XDG_RUNTIME_DIR/cdimport/drive-<sr0>.lock`, held from the disc preview
  to the end of the rip). Another cdimport skips that drive, also for the
  ioctl fallback of detection. Found 2026-10-12 when a test run's `cyanrip
  -I` ran on the drive during the maintainer's rip (AccurateRip still
  matched 6/6). Versions before this fix do not take the lock.
- **Lyrics mode** chosen by the user each run: automatic (`lyrembed DIR
  --auto`) or interactive (`lyrembed DIR`). From lyrembed 0.8.0 the
  interactive mode is a page in the browser; cdimport still starts
  `lyrembed DIR` and waits for it to exit (Quit on the page, or closing the
  tab, ends it), so only the texts differ (`lyrics.probe`,
  `Session.lyrembed_page`, `app.page_hint`).
- **Tag editor**: Ear Tag by default, others selectable (Kid3, Picard),
  configurable as a command template, or skip.

### Installing cyanrip

Never install silently: show the exact command, ask, and use `sudo` only for
system package managers. A missing optional tool disables exactly its step.

| Platform | Route |
|---|---|
| Debian/Ubuntu and derivatives | `apt install cyanrip` |
| Arch | AUR only (`yay -S cyanrip`) |
| openSUSE Tumbleweed | `zypper install cyanrip` |
| Alpine / Void / FreeBSD / NixOS | `apk` / `xbps-install` / `pkg` / `nix` |
| Distros without a package (**Fedora**) | **user chooses the folder** for the source; clone, build with meson into `~/.local`, remember the folder in the config |
| Windows (later) | **latest exe from GitHub releases first** (release build; GitHub publishes no nightly or master exe); scoop if available |

Detect the distro from `/etc/os-release` (`ID`, `ID_LIKE`), the platform from
`sys.platform`. Packaging status was taken from Repology on 2026-10-11
(`https://repology.org/api/v1/project/cyanrip`); re-check rather than trust it.

Fedora build dependencies (all in Fedora 45, `ffmpeg-devel` in RPM Fusion):
`meson ninja-build gcc ffmpeg-devel libcdio-paranoia-devel
libmusicbrainz5-devel libcurl-devel`, optional `qrencode-devel
libchromaprint-devel`. cdimport checks them with pkg-config against the
versions in cyanrip's `src/meson.build` and installs only what is missing.
On 2026-10-11 the maintainer's PC lacked `meson ninja-build
libmusicbrainz5-devel libcurl-devel` (all in the `fedora` repo, checked with
dnf repoquery); FFmpeg 9 and libcdio-paranoia headers were installed.

Other distributions (`install.dependency_command`, checked 2026-10-27 in
podman containers by installing exactly cdimport's command, then checking
every pkg-config requirement and building cyanrip): `apt` for
debian/ubuntu (and ID_LIKE, e.g. Mint) with `-dev` names, `pacman -S
--needed` for arch (its packages carry the headers; FFmpeg is one package),
`zypper` for suse/opensuse with `pkgconfig(NAME) >= VERSION`: Tumbleweed
offers FFmpeg 4 to 9 for each, and the version makes it pick 9. master
(648b1dd) built on Debian 13, Ubuntu 24.04, Arch and Tumbleweed; v0.9.3.1
built on Debian and Ubuntu (FFmpeg 7.1/6.1) but not against FFmpeg 9 (Arch,
Tumbleweed), as on Fedora. Distros without a mapping (Gentoo, ...) get
the list of pkg-config names, as before.

### Updates of cdimport itself

Asked for by the maintainer on 2026-10-27, after picktag got the same
(`picktag/core/selfupdate.py`; copied, not imported, like everything shared
between the tools). `selfupdate.latest` takes the highest plain `x.y.z` tag
from `git ls-remote --tags` of the private repository: git has the access
`pipx install` has (gh as credential helper) and never prompts
(`GIT_TERMINAL_PROMPT=0`, no askpass). Step 0 asks at most once a day
(`<cache>/self-update.json`, written when GitHub answered), or at once with
`--update`; never when started with `--return-url` (lyrembed's Import a
CD runs the show then). A pipx install (`pipx_metadata.json` in
`sys.prefix`) is updated in the terminal after a yes: `pipx upgrade`, or
`pipx install --force` with the new tag when the install is pinned to one.
Then `app.Restart` reaches `cli.main`, which `exec`s the command cdimport
was started with, without `--update`. The page only shows the command:
restarting would cut it off, and pipx replaces the page's files under the
running server. A checkout gets `git pull`, another install the pipx
command. Checked 2026-10-27 against GitHub and the real pipx metadata, and
in a pseudo-terminal with a faked release (question, run, exec).

### Update notification for cyanrip

At most once a day, result cached (e.g. `~/.cache/cdimport/`):

- **Self-built:** compare the version and commit (`cyanrip -h`'s first line) with
  `git ls-remote` of GitHub (newest `vX.Y.Z` tag, `master` commit); behind
  → offer to rebuild. Built that way instead of `git fetch` in the source
  folder, so it also works for a cyanrip built by hand.
- **Channel mismatch** (a release installed, `channel: master`): offer the
  master build on every start, not once a day.
- **Distro package:** the package manager handles updates. Only say when
  GitHub has a newer release than the installed version, compared on the
  first three numbers: v0.9.3.1's meson.build says 0.9.3, which is what
  Debian's and openSUSE's package print (Ubuntu 24.04's 0.9.2 says 0.9.0).
- **Windows exe:** compare with GitHub's latest release, offer the download.

## Facts checked on 2026-10-11

Behaviour depends on third-party tools; re-verify before relying on these.

**cyanrip** (github.com/cyanreg/cyanrip)
- Last release v0.9.3.1 (2024-06-05), but very active: ~95 commits since
  July 2026, `master` is at `0.9.4-rc3`. As of 2026-10-10 it also writes the
  MusicBrainz recording ID as `musicbrainz_trackid`, which lyrembed reads.
- **Drive offset is mandatory for ripping** (0.9.4-rc3, checked
  2026-10-11): without `-s` cyanrip exits 1 with "Offset is unset! To
  continue with an offset of 0, run with -s 0!", although `-h` says
  "default: 0". `-I` works without it. cdimport asks before the first rip
  (measure with `-f`, enter, or 0 for one rip) and saves `cyanrip.offset`.
- The maintainer's ASUS SDRW-08D2S-U is "[Purged]" in AccurateRip's drive
  list (no constant offset across drives of that name), so it must be
  measured. `cyanrip -f` with the 6-track disc in the drive on 2026-10-11
  failed: "No track had AccuRip entry, cannot find offset!"; it wrote no
  files. Needs a disc that is in the AccurateRip database. Later that day,
  with another disc, it found **+6** (confidence 10), saved as
  `cyanrip.offset: 6` in the maintainer's config.
- `cyanrip -f` ends with `Drive offset of +6 found (confidence: N)!`, where
  N counts the tracks that confirmed it (`src/cyanrip_main.c`). cdimport
  parses that line and saves the offset without asking from N >= 4
  (`app.MIN_OFFSET_CONFIDENCE`); below, it asks.
- **Disc not on MusicBrainz** (0.9.4-rc3, checked 2026-10-12 with a
  3-track disc): `-I` exits 1 with "Unable to find release info for this
  CD", prints the `https://musicbrainz.org/cdtoc/attach?toc=FIRST+LAST+...`
  link and "To continue add metadata via -a or -t, or ignore via -N!".
  With `-R <release MBID>` it takes the release's tags anyway
  (`mb_release_by_id`, says "The disc isn't on MusicBrainz yet"). `-a`/`-t`
  are parsed with FFmpeg's `av_dict_parse_string` ("key=value:key=value",
  `-t N=title=...`); cyanrip's `append_missing_keys` only understands
  backslash escapes, so `rip.tag_args` escapes `\ ' : =` with a backslash
  (round trip checked against libavutil). Keys: `album`, `album_artist`,
  `date`, `title`. cdimport reads the link and track range from all of
  `-I`'s output (`rip.parse_preview`); the link comes before the track list.
- **Several releases for the disc ID:** `-I` (and a rip) exits 1 after
  "Multiple releases found in database for DiscID ...:", one line per
  release `    N (ID: <mbid>): Album (disambiguation) (country) (N CDs)
  (date)`, and "Please specify which release to use by adding the -R
  argument". `rip.parse_preview` reads those lines into `Preview.releases`.
  Seen 2026-10-12 with a real disc ("Back to Life", three releases); the
  format matched `src/musicbrainz.c`.
- Attaching a disc ID on MusicBrainz ("Add disc ID", edit type 55): 7-day
  voting period, accepted on expiry; MusicBrainz's guide shows it applied at
  once in its example. Steps from musicbrainz.org/doc/How_to_Add_Disc_IDs.
- **Rip speed** (measured 2026-10-14, ASUS SDRW-08D2S-U, track 1 of a
  16-track disc, 3:48): `-P none` 113 s, `-P max` (cyanrip's default:
  `PARANOIA_MODE_FULL ^ NEVERSKIP`) 200 s; both matched AccurateRip
  (confidence 71). 18-25 s pass before the first sector. Even with `-P
  none` the drive reads ~1200 sectors in ~3.5 s, then stalls ~3.3 s, so it
  peaks near 2.5x; paranoia added one 22 s stall (re-reads). The
  maintainer's 13 logs so far: 1.5-2.8x real time, 0 errors. Hence fast
  mode (`cyanrip.mode`, decided 2026-10-14): `-P none`, then
  `rip.accuraterip_results` reads each track's "(accurately ripped" from
  the log (matched cyanrip's own "Tracks ripped accurately" on all 13
  logs), and `rip.rerip` rips unconfirmed tracks with `-P max -l N,...`.
  A fast rip runs without `-Q`; `disc.eject` ejects afterwards, or the
  re-rip's `-Q` does. 2026-10-14 the first real fast rip (9/9 confirmed)
  left the disc in: the single CDROMEJECT failed, presumably EBUSY (the
  kernel ejects only when nobody else has the drive open, and udev/udisks
  look at it after cyanrip closes it; not reproduced). `disc.eject` now
  retries EBUSY 5x 1 s, then runs util-linux `eject` (SCSI fallback, as
  libcdio's `cdio_eject_media` does for cyanrip's `-Q`). "AccurateRip:" in `-I` must say
  "found", else the rip is secure. Not yet run end to end on real
  hardware: the fast/re-rip file names are assumed to match because both
  passes use the same release, tags and naming scheme.
- **Older releases** (checked 2026-10-27 in the v0.9.2/v0.9.3.1 sources
  and with Debian 13's, Ubuntu 24.04's and Tumbleweed's packages): no `-v`
  (`invalid option`, exit 1; cdimport reads `-h`, whose first line is
  `cyanrip VERSION (TAG) help:` there and `cyanrip VERSION (COMMIT)` on
  master). Every line cdimport parses (offset, unknown disc, several
  releases, AccurateRip, "Track N ripped and encoded successfully!") is the
  same; "with errors." exists only from 2025-10. `-R` for a disc
  MusicBrainz does not know needs master bde6a1e (2026-10-10, first in
  0.9.4-rc3, `install.RELEASE_FOR_UNKNOWN_DISC`); before, it only picks
  among the disc ID's releases, so cdimport hides `s`/`m` for such a disc.
- Relevant options: `-I` print disc info and MusicBrainz match without
  ripping; `-R` pick a MusicBrainz release (index or ID); `-D`/`-F` directory
  and file naming schemes (use `-D` to know the output folder instead of
  guessing); `-Q` eject on success; `-o flac` (default); `-W` disable
  automatic deemphasis; `-K` disable ReplayGain; `-N` no MusicBrainz;
  `-d` device; `-s` drive offset; `-f` find drive offset.
- Defaults compared with wav-to-flac (`COMPRESSION_LEVEL=8`, bit-exact only):
  - FLAC compression is fixed at 11 (FFmpeg-specific level, not adjustable).
  - **Preemphasis:** on discs flagged with it, cyanrip applies a deemphasis
    filter by default, which changes the audio data. `-W` keeps it as-is.
    **Decided 2026-10-11: use cyanrip's default** (deemphasis on, config
    `cyanrip.deemphasis: true`); players ignore the flag, so bit-exact
    rips of such discs sound too bright. `false` passes `-W`.
  - ReplayGain tags are written by default (tags only). Make it configurable.
  - Output is 16-bit for normal CDs (more only with HDCD `-H` or deemphasis).
- Not packaged for Fedora, RPM Fusion, Terra or Flathub. The repo has a
  Flatpak manifest (`com.github.cyanreg.cyanrip.yaml`), but the maintainer
  chose the native source build for Fedora.

**Ear Tag** (Flatpak `app.drey.EarTag`, 1.0.3 installed)
- Registers `HANDLES_OPEN`, so files can be passed on the command line
  (`flatpak run app.drey.EarTag FILE...`). Whether it accepts a folder is
  untested.
- Its sandbox only has `xdg-download` and `xdg-music`, so files in `~/Temp`
  are invisible to it. cdimport passes them with `flatpak run
  --file-forwarding app.drey.EarTag @@ FILES @@` (document portal). Whether
  Ear Tag can save through the portal is untested.
- Single-instance GApplication: if a window is already open, the launch
  command returns at once. Do not wait for the process; ask the user to
  press Enter when done editing. Afterwards re-read the tags and warn about
  tracks without artist or title (lyrembed's search needs both).

**Cover art** (checked 2026-10-11)
- cyanrip embeds only an image the Cover Art Archive marks as *front*. For
  P1Harmony "때깔 (Killin' It)" (색깔 (Superb) ver., release 5c580fb1-…) the
  only image is untyped, so cyanrip logged `Album Art: none`; the release
  group has a front cover from another edition. cdimport (`cover.py`)
  offers both, or a file, when tracks lack a picture, embeds the 1200 px
  version as front cover (never replacing a picture) and saves `cover.jpg`
  unless one exists. Offered whenever cdimport works on an album: after a
  rip and each time an album is opened again (resumed or `cdimport
  FOLDER`), at whatever step. Tracks without a picture get the offer;
  when all have one, keep (default) or change it. Changing swaps only the
  front-cover picture (type 3) and replaces cover.jpg/.png.
- Also a separate command, `cdimport-cover FOLDER... [-r] [--release ID]`.
  Without a release ID in the tags it searches release groups by the album
  and album-artist tags (again without a trailing "(...)" when nothing is
  found) and asks which hit is right. MusicBrainz gets at most one request
  per second and a 503 is retried twice; its User-Agent is
  `cdimport/<version>` without contact details (never put the maintainer's
  email there without asking). A 503 seen 2026-10-11 was temporary.

**lyrembed** (`~/Projekte/Git-Repos/lyrembed`, private repo `sobakah/lyrembed`,
installed with pipx)
- 2026-10-11: Fedora moved `/usr/bin/python3` to 3.15 and removed 3.14, so
  every pipx venv built on 3.14 (lyrembed, keepbest, media-optimizer) fails
  with "bad interpreter". cdimport crashed on it; now step 0 runs
  `lyrembed --version` / `cyanrip -h`, reports such a tool with the repair
  command (`pipx reinstall NAME`), and any tool that cannot start returns
  127 instead of raising.
- Exit codes: 0 done/quit, 1 batch failures, 2 unusable input, 130 Ctrl+C.
  **Batch mode exits 0 also when it embedded nothing** (checked 2026-10-15
  in 0.7.0's `run_batch`): "no confident match", incomplete tags and
  instrumentals count as skipped; 1 means only unreadable files or failed
  writes. There is no machine-readable summary. So cdimport reads the
  tracks after `--auto` (`lyrics.without_lyrics`: Vorbis `LYRICS`/
  `UNSYNCEDLYRICS`/`SYNCEDLYRICS`, ID3 `USLT`/`SYLT`, MP4 `©lyr`, as
  lyrembed's `tagging.py`) and offers interactive mode for what is left.
- Tags only `.flac .mp3 .ogg .opus .m4a` (`SUPPORTED_EXTENSIONS`); cdimport
  skips the lyrics step for albums with none of them.
- Batch mode (`--auto`) never prompts; `--dry-run`, `--overwrite`,
  `--color never` exist.
- Planned in lyrembed, not built yet: "automatic, then prompt only for the
  tracks it could not decide" (on the page: open it with only those
  tracks), and a "Done – back to cdimport" button with a return address.
  When they exist, offer the first as a third lyrics mode.
- **lyrembed 0.8.0** (2026-10-21) replaced its terminal menus with a page in
  the browser. Its CLI flags, `--auto` and exit codes are unchanged; it
  prints the page's address and waits. `lyrics.probe` recognises the page
  by `--no-browser` in `lyrembed --help`.
- **Romanization in batch mode** was added to lyrembed on 2026-10-11 at the
  maintainer's request (released as lyrembed 0.6.0): `settings.auto_romanize` (default false) and `--romanize` /
  `--no-romanize` for `--auto`, reusing its bulk-action `auto_romanize`.
  cdimport asks in automatic mode; Enter passes no flag, so lyrembed's own
  setting decides. It only asks when `lyrembed --help` lists `--romanize`
  (`lyrics.probe`); older versions would reject the flag.

**Disc detection (Linux)**
- udev reports audio discs: `ID_CDROM_MEDIA_TRACK_COUNT_AUDIO` in
  `udevadm info -q property -n /dev/srN`. Alternative without external
  commands: the `CDROM_DISC_STATUS` ioctl via `fcntl`.
- Tested 2026-10-11 on the maintainer's external ASUS SDRW-08D2S-U
  (`/dev/sr0`, 6-track audio CD): udev and the ioctl both report it as audio.
- Windows (later): `GetDriveTypeW == DRIVE_CDROM` via `ctypes`; audio CDs
  show `.cda` files.

## Planned behaviour worth building in from the start

- **Resumable:** a small state file per album records finished steps; an
  interrupted run offers to continue (e.g. with tagging) next time.
- **Config file** for working folder, library, editor commands, cyanrip
  options, default lyrics mode, cyanrip source and build folders.
- **Ctrl+C** at any step stops cleanly and leaves a resumable state.
- No disc in the drive → wait, or let the user pick an existing folder to
  continue with steps 3–5 (covers albums ripped earlier).

## Environment of the maintainer

Fedora 45, fish shell, VS Code. FFmpeg 9 and `cd-paranoia` installed;
`meson`, `ninja-build`, `libmusicbrainz5-devel`, `libcurl-devel` not yet.
**Verified 2026-10-11:** v0.9.3.1 does not compile against FFmpeg 9
(`AVCodec has no member ch_layouts` in `cyanrip_encode.c`); master
(0.9.4-rc3, b40c1f0) builds. The maintainer's config therefore needs
`"cyanrip": {"channel": "master"}`. `cyanrip -v` prints
`cyanrip 0.9.4-rc3 (b40c1f0)`. The repository's `nightly` tag is from
2020 (261 commits behind master), and GitHub has no nightly release, only
versioned ones with a Windows exe. So the channel is called "master", not
"nightly" (maintainer's decision, 2026-10-11).

## Front ends

Every message and question goes through `ui` (`info`, `warn`, `check`,
`choose`, `confirm`, `ask`, `ask_path`, `wait_for_enter`, ...), and an
external tool's output through `ui.tool_output` (`proc.run_tail`). Without a
front end that is the terminal, unchanged. `ui.use(front)` hands every call
to a `ui.Front` instead, with its plain content, which is how the browser
page runs the same flow: the flow in `app.py` stays
one, and only the front end differs. So never `print()` or `input()`
outside `ui`, and keep prompts' options and defaults as data, not text.
A front end that wants live tool progress keeps the pseudo-terminal
(`Front.wants_pty`): in a pipe, cyanrip buffers its progress display.

**The page (`--web`, preview)** is `web.py` plus `static/`. `web.Run` runs
`cli.run_session` (the terminal's flow and ending) in a thread with
`WebFront` as the front end:
- `WebFront` keeps the messages and a tool's output as events with revision
  numbers. The page long-polls `/api/state?since=N`, and a question blocks
  the flow until `/api/answer`, which is checked against the open question.
- A tool's output event holds its lines. A line a progress display redraws
  with `\r` counts only in its newest state; the event's `partial` shows it
  until a real line break comes.
- The flow starts on the page's first visit (`Run.start`), not at launch,
  so nothing runs ahead of what the page can show.
- Stopping (`WebFront.stop`) is final: the open question, every later one
  and a pause (`ui.pause`, the disc wait) raise `Cancelled` (a
  `KeyboardInterrupt`), so the flow ends as on Ctrl+C (130). While a tool
  runs, `proc.interrupt_tool` sends it SIGINT first, and cyanrip finishes
  its log. A page that is gone does not stop a running tool. Ctrl+C in
  `serve`'s terminal already reached the tool (same process group,
  `proc.terminal_interrupts_tools`), so it gets no second SIGINT: cyanrip
  quits on that without finishing its log ("Force quitting",
  `on_quit_signal`, checked 2026-10-25).
- Anything that may ask for a sudo password stays in the terminal: on the
  page step 0 only names `cdimport --install-cyanrip`. lyrembed before 0.8.0
  asks in terminal menus, so the page offers no interactive lyrics with it
  (`app.interactive_lyrics`).
- Security as in lyrembed: 127.0.0.1, the token as an HttpOnly SameSite
  cookie, `Host` and `Origin` checks, JSON only, a CSP. Installing cyanrip
  stays in the terminal, because it may ask for a sudo password.
- Pictures: `ui.choose(..., info={"images": {key: {"url"|"file": ...}}})`
  gives an option a picture; the terminal ignores `info`. `WebFront`
  registers each source under a random id, and `/api/image/<id>` serves only
  those, so the page cannot make cdimport fetch arbitrary addresses. A file
  (the cover preview, deleted after its question and rewritten under the
  same name) is read when registered, under a new id each time. Only
  JPEG and PNG are served (`cover.image_type`), and downloads are cached.
  Release thumbnails come from `cover.release_thumbnail` (Cover Art Archive,
  404 when there is none).
- The tag editor (`tageditor.py`, no UI) replaces Ear Tag on the page:
  `ui.supports("tag_editor")` adds the option "w" to the tags step and makes
  it the default, and `ui.edit_tags` returns the folder, renamed if the
  album changed, and what comes next: `"done"` (Continue ends the step, no
  return to the menu, unlike an outside editor) or `"cover"` ("Change the
  cover…" saves, runs `offer_cover` again and reopens the editor). It shows
  every tag in the files: the fields it edits (with disc and track number),
  and the rest under "More tags" (MusicBrainz IDs and the like), album-wide
  when equal in every track, else per track. It can change or empty those
  but never add new keys; lyrics tags are lyrembed's and only noted. The
  embedded front cover (picture type 3, else the first) is shown once when
  every track has the same, else per track, via `/api/tags/cover` (JPEG and
  PNG only). A save that renames the folder calls `edit_tags`'s `moved`
  at once, so the flow knows the folder even if it is stopped before
  Continue. Changing a track number does not rename the file. Several artists follow Picard: `ARTIST` is the credit,
  `ARTISTS` holds the names; the same for `ALBUMARTIST`/`ALBUMARTISTS`.
  cyanrip writes the credit only. Names follow the tags only where cyanrip
  built them (`{track} - {title}`, `{album}...`, its default "unicode"
  replacements copied in `tageditor.cyanrip_name`); anything else keeps its
  name. The `.cue` sheet's FILE lines follow, and `.cue`/`.log` follow the
  album; the log's content is never changed (its checksum). Everything is
  checked before anything is written, and renames go through temporary
  names, so two tracks that swap titles do not overwrite each other.
- The lyrics step hands the tab to lyrembed's page, if lyrembed has
  `--return-url` (`lyrics.probe`, from its `--help`; lyrembed 0.9.0):
  - cdimport starts `lyrembed DIR --no-browser --return-url <its own page>
    --return-name cdimport` with `ui.tool_start(..., handoff=True)`;
  - `WebFront` reads lyrembed's address from its output line "lyrembed is
    running at <url>" (`_HANDOFF`) and adds a `handoff` event, and the page
    follows it once (remembered in `sessionStorage`, so coming back does not
    send it there again);
  - lyrembed's "Done – back to cdimport" ends lyrembed, and `run_lyrembed`
    returns.
  Leaving sends cdimport's own "closing", so the page does not count as
  gone while it is away or for `HANDOFF_GRACE` after (`WebFront.away`). On
  the page, lyrembed's output (also `--auto`'s) goes to it through
  `run_tail`; in the terminal lyrembed still gets the terminal (`proc.run`).
  An older lyrembed opens a tab of its own, as before.
- Started by lyrembed's "Import a CD" (`--return-url URL --return-name
  lyrembed`, with `--web` only, local addresses only, `cli.local_page`),
  the finished run's button leads back there. `serve` ends by printing
  "cdimport finished: <album folder>" (`FINISHED_LINE`), which lyrembed
  reads to open the album: keep that wording. The token cookie's name
  carries the port (`web.cookie_name`), as in lyrembed: cookies belong to
  a host, not a port, and two pages on 127.0.0.1 overwrote each other's.
- The page scrolls like a terminal: it follows new content only while the
  view is at the end, and a reader who scrolled up stays put. The same holds
  inside a tool's output box. A ResizeObserver catches growth after render
  (a cover that loads late), and the page's own `scrollTo` is flagged
  (`ownScroll`): its scroll event can arrive after the page grew again and
  would otherwise count as the reader leaving the end.
- `static/vendor/preact-htm.js` is the file lyrembed vendors (htm 3.1.1's
  Preact bundle, checksum and licenses in `LICENSES.txt`).

## Conventions (shared with lyrembed)

- **Errors are reported, never swallowed.** No bare `except: pass`.
- **Optional dependencies stay optional.** A missing tool or package disables
  exactly one feature and says so where it is used.
- **Never install, delete or overwrite without asking.**
- **Tests never touch the network or real hardware.** Mock subprocess calls,
  the drive and HTTP. The autouse fixture `no_real_tools` fails a test that
  starts cyanrip, eject, lyrembed, flatpak, git, a package manager, ... by
  name or outside the test's temp folders (2026-10-15: a test whose fake
  replaced `proc.run`, after `run_tail` stopped calling it, started the
  real `cyanrip` on the empty drive). Fake `proc.run_tail` for cyanrip.
- **Type hints and docstrings** on public functions. Docstrings say *why*.
- **Verify claims, do not assert them.** Run it before stating that it works.

## Notes outside the repo

Commit message drafts and release notes go to
`~/Software/Claude/Projects/cdimport/` (`COMMIT_MSG_<version>.txt`,
`RELEASE_NOTES_<version>.md`), never into the repository. Version tags have
no `v` prefix and three parts (`0.1.0`), like lyrembed.

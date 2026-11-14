# cdport

Rips an audio CD into a tagged, lyrics-embedded FLAC album, in the terminal
or on a page in the browser. It is glue: it drives existing tools in order and
does not rip, tag or fetch lyrics itself.

```
0. Tools    cyanrip, a tag editor, lyrembed: found or not; offers to install cyanrip
   Start    the disc in the drive, an unfinished album, any album folder, or only its tags
1. Disc     wait for an audio CD, preview the MusicBrainz match (cyanrip -I)
2. Rip      cyanrip → FLAC with tags, cover, AccurateRip log
   Cover    offered whenever an album is opened
3. Tags     cdport's own editor on the page, or Ear Tag, Kid3, Picard; then a tag check
4. Lyrics   lyrembed, automatic or interactive (on lyrembed's page in the browser)
5. Finish   offer to move the album into the music library, summary, back to Start
```

Python 3.10 or newer · Linux (Windows later)

## Installing

```bash
pipx install git+https://github.com/sobakah/cdport.git
cdport --install-desktop-entry   # optional: cdport (its page) in the app menu
cdport --install-dbus-service    # optional: for Jewelcase and other programs
```

pipx installs the Python packages cdport needs (`mutagen` for tags, `jeepney`
for D-Bus). The other programs:

- [cyanrip](https://github.com/cyanreg/cyanrip) for ripping. Without it,
  cdport still continues albums ripped earlier.
- A tag editor for the terminal: [Ear Tag](https://flathub.org/apps/app.drey.EarTag)
  by default, Kid3 or Picard as alternatives, or none. Without Ear Tag (it
  only comes as a Flatpak), cdport offers an installed Kid3 or Picard. On the
  page cdport has its own editor.
- [lyrembed](https://github.com/sobakah/lyrembed) for lyrics (optional).

### Installing cyanrip

When cyanrip is missing, cdport offers to install it. It shows every
command first and asks before running it. Where the distribution has a
package, it uses the package manager (`sudo apt install cyanrip`, ...). On
Fedora and other systems without one, it builds cyanrip from source:

1. It checks the build dependencies with pkg-config and offers to install
   the missing ones with the package manager: `dnf` (Fedora), `apt`
   (Debian, Ubuntu, Mint), `pacman` (Arch, Manjaro) or `zypper`
   (openSUSE). Elsewhere it lists them and waits until they are installed.
2. It asks for the clone folder and the meson build folder. Both prompts
   are prefilled and editable (Tab completes paths): the folders remembered
   from the last build, else `~/.local/src/cyanrip` and its `build`
   subfolder.
3. It clones the repository, then builds and installs with meson into
   `~/.local` (no sudo).
4. It saves both folders as `cyanrip_source_dir` and `cyanrip_build_dir` in
   the config.

Which version is set by `cyanrip.channel` in the config:

- `"release"` (default): the latest release, from the distribution package
  or as a source build of the newest `vX.Y.Z` tag.
- `"master"`: the `master` branch, always built from source into
  `~/.local`, also where a package exists. Use it when the release does not
  build against your FFmpeg: v0.9.3.1 does not compile against FFmpeg 9
  (Fedora 45, Arch, openSUSE Tumbleweed).

At start cdport compares the installed cyanrip (from `cyanrip -h`) with
GitHub, which it asks at most once a day:

- A release installed with `"master"` set: it offers to build master
  (on every start, since the config asks for it).
- An older release than the latest, or a master build behind `master`: it
  offers to rebuild a cyanrip in `~/.local`. For a distribution package it
  only tells you; the package manager does the update.

`cdport --install-cyanrip` updates and rebuilds a source build (or
reinstalls the package). For a missing editor or lyrembed, cdport only
shows how to install it.

On the page all of this works too: the page runs the source build and shows
its output. A package manager's command (the build dependencies, or the
distribution's cyanrip package) asks for your password and a yes, which a
page cannot pass on. The page shows that command to run in a terminal, waits
until you say it has finished, and then checks what it installed.

### Fedora Silverblue, Bluefin and other image-based systems

cyanrip has to be built, and the system image has no compiler, so install cdport **in a
toolbox** (or a distrobox), where it builds cyanrip as on Fedora:

```bash
toolbox create cdport && toolbox enter cdport
sudo dnf install pipx systemd-udev
pipx install git+https://github.com/sobakah/cdport.git
pipx install "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git"
cdport --install-desktop-entry
cdport --install-dbus-service
```

- **The app menu entry and the D-Bus service** made inside the container start cdport in it
  (`toolbox run --container cdport …`, `distrobox-enter -n …` for a distrobox), so Jewelcase on
  the host imports CDs as usual. Start cdport from the menu or from a terminal in the
  container: pipx's folders are in the home folder, which the host shares, so `cdport` is on
  the host's `PATH` too, but there it finds no cyanrip and says so.
- **The page opens in the host's browser**, and Ear Tag (a Flatpak) is the host's.
- **The drive** is seen in the container as on the host. `systemd-udev` lets cdport name its
  model; without it, cdport still finds the disc.
- **lyrembed** runs where it is started. Make its app menu entry and D-Bus service in the same
  container (`lyrembed --install-desktop-entry`, `lyrembed --install-dbus-service`), so its
  *Import a CD* starts cdport there.

## Using it

```bash
cdport                                  # start: rip the disc in the drive, or pick an album
cdport ~/Temp/Album                     # continue with an album ripped earlier
cdport ~/Temp/Album --step lyrics       # begin with that step (cover, tags, lyrics, finish)
cdport ~/Music/Artist/Album --edit-tags # only edit an album's tags, then end
cdport --lyrics auto --editor skip
cdport --rip-mode secure                # paranoia for every track (default: fast)
cdport --install-cyanrip                # update and rebuild cyanrip first
cdport --update                         # look for a newer cdport now
cdport --web                            # the same run on a page in the browser
```

From a checkout, `./run.py` starts cdport without installing it.

On first use cdport asks for two folders and saves them in the config: the
working folder, where rips land first (suggested: `~/Temp`), and the music
library (suggested: your desktop's music folder, e.g. `~/Music`). `-w DIR`
and `-l DIR` use other folders for one run.

### Where a run begins

A run begins at **Start** and comes back there after each album, until you
quit. Start offers:

- **rip the disc** in the drive (the default when there is one; otherwise
  cdport waits for a disc),
- **continue an unfinished album** from the working folder, with its next
  open step,
- **open an album folder**, any folder with audio files, to go through its
  open steps (an album already in the library, say),
- **edit the tags of an album folder**: only the tag editor, then back to
  Start. Nothing is written into the folder but the tags you save.

Within an album the steps follow in order (cover, tags, lyrics, finish).
On the page any of them can be chosen instead (see below), and the order
goes on after it. Finishing an album counts the steps left out as skipped.
`--step` does the same from the command line, and `--edit-tags` is the
tag editor alone, as Jewelcase's **Edit Tags** uses it.

Every unfinished album in the working folder gets a small hidden state file
recording the finished steps. When a run is stopped (Ctrl+C, or `q` at a
prompt), the next run offers to continue it. The file is deleted when the
album is finished.

### On a page in the browser (preview)

```bash
cdport --web               # the same run on a page in your default browser
cdport --web --no-browser  # print the page's address instead of opening it
```

`--web` runs exactly the terminal's steps, with a page as their front end:
a step bar, each step's messages, cyanrip's output live (with **Stop
cyanrip**, which ends it like Ctrl+C, so it finishes its log), and each
question as buttons or a field; the terminal's keys work there too. Only
this computer can open the page, and only with the address cdport
prints. Closing the tab stops cdport at its next question, never during
a rip. On the page:

- **You don't have to go in order.** While an album is open, a bar above
  the steps shows its cover and folder, and **Tags**, **Lyrics** and
  **Finish** in the step bar go straight to that step (Tags opens the
  editor at once), done or not; **Cover…** changes the cover and comes
  back. **Start** in the header leaves the album as it is, resumable, for
  another disc or album. None of this works while a tool runs (a rip, or
  lyrembed). Unsaved changes in the tag editor are asked about first.
- **Start shows the unfinished albums with their covers**, and asking for
  an album folder lists folders to click through (the working folder, the
  library, home).
- **Releases, albums and covers show their pictures** (from the Cover Art
  Archive; cdport fetches only the pictures its questions name).
- **The tags step has its own editor**, offered first; Ear Tag and co.
  stay as alternatives. It edits FLAC, MP3 (ID3, kept in its version),
  Ogg Vorbis, Opus and M4A files, also mixed in one album: album, album
  artist, date, genre and each track's title, artist, disc and track number,
  and it lists every other text tag in the files (MusicBrainz IDs and the
  like) to change or remove, under Picard's names where it has one
  (`COMPOSER` for an MP3's TCOM). A value a format cannot hold (a date
  that is no date in an MP3, a track number that is no number in an M4A)
  is pointed out before anything is saved. It shows the embedded cover;
  **Change the cover…** goes back to the cover step and then returns to the
  editor. Several artists follow Picard: `ARTIST` holds the credit ("A
  feat. B"), `ARTISTS` the single names (and the same for
  `ALBUMARTIST`/`ALBUMARTISTS`); "Move feat. from titles to artists" and a
  search and replace in all titles help. File names follow a changed title,
  and the folder a changed album, where cyanrip built them; the cue sheet
  follows, the rip log is never changed.
- **The lyrics step hands the tab to lyrembed's page**: **Done – back to
  cdport** there brings it back, and the run goes on.

### App menu

```bash
cdport --install-desktop-entry
```

adds cdport to the app menu of GNOME, KDE and other freedesktop.org
desktops: `~/.local/share/applications/cdport.desktop` and an icon under
`~/.local/share/icons/`. Started from the menu, cdport opens its page in
your default browser (`--web`). The entry starts the cdport that wrote it
(a pipx installation, or the Python and `run.py` of a checkout), so run the
command again after installing cdport somewhere else.
`--remove-desktop-entry` removes both files.

### Updates

At every start cdport looks for a newer release on GitHub, in the
background, so the start doesn't wait (`git ls-remote`, with the same access
as `pipx install`; it never asks for a password). When an earlier start found
one, step 0 says so with a link to what is new; one found while cdport
runs is named at the end of the run. Installed with pipx, cdport offers to
update right away, in the terminal and on the page (`pipx upgrade cdport`,
or `pipx install --force ...@<new tag>` for an install pinned to a release,
which stays pinned). From step 0 it then starts again with the same
arguments, and the page reloads by itself; at the end of a run the next start
uses the new version. It never updates during a rip. Other installs get the
command to run. `cdport --update` looks right away and waits for the
answer; `"check_for_updates": false` in the config turns the check off.

## Ripping

### Fast and secure rips

By default cdport rips **fast**: cyanrip reads without paranoia
(`-P none`), and afterwards every track is checked against the AccurateRip
database. Tracks it does not confirm are ripped again with full paranoia
(`-P max`) and replace the fast ones; that log is kept as
`<album> (secure re-rip).log`. A disc that is not in the AccurateRip
database cannot be checked, so it is ripped securely right away.

**Secure** mode uses cyanrip's paranoia for every track: each block is read
in overlapping, verified pieces, and the drive's cache is defeated by
seeking away and back. On a slim USB drive that takes about twice as long.
Set `cyanrip.mode` to `"secure"`, pass `--rip-mode secure` for one run, or
press `p` in the disc menu for one disc.

cyanrip refuses to rip without the drive's read offset. Before the first rip
cdport offers to measure it (`cyanrip -f`, which needs a disc in the
AccurateRip database), to type it in, or to use 0 for one rip; a measured
offset that at least 4 tracks confirm is saved without asking.

cyanrip writes R128 gain tags 10 dB too high next to its correct ReplayGain
tags, and players that prefer R128 tags would play the album too loud. cdport
deletes the R128 tags after each rip; the ReplayGain tags say the same.

### Several releases for one disc

When several MusicBrainz releases have the disc's ID (editions, reissues),
cyanrip lists them and waits for a choice. cdport shows them as numbered
options with a link to each release page; pick one to see its tags, then rip.

### Discs MusicBrainz does not know

cyanrip looks the disc up by its disc ID. When MusicBrainz has no release with
that ID, cyanrip will not rip without tags, and cdport offers:

- `s` search MusicBrainz for the release by album and artist. Each hit shows
  its media and track counts (the disc's count is marked) and a link.
- `m` a release ID or a pasted MusicBrainz release link. cyanrip takes the
  tags from that release even though the disc ID is not attached to it.

  `s` and `m` need cyanrip 0.9.4-rc3 or newer for such a disc. The
  distributions' packages (0.9.3.1 and older) take a release only for discs
  MusicBrainz knows, so with them cdport offers `t` instead.
- `t` type in the tags: album, album artist, year, track titles (cyanrip's
  `-a`/`-t`). The tag step can refine them.
- `n` rip without MusicBrainz metadata.

It also shows cyanrip's link for adding the disc ID to MusicBrainz (needs a
MusicBrainz account). Open it while logged in, search for the release, select
the edition with the same number of tracks and attach the disc ID; if the
release is missing, add it there. Enter an edit note and submit. Once the edit
is applied (it may wait up to 7 days for votes), cyanrip finds the disc by
itself, for you and everyone else.

## Covers

If the tracks have no embedded cover (cyanrip only takes an image the Cover
Art Archive marks as front cover), cdport offers what the archive has for
the release or another edition of the album, or a picture file of your own.
It embeds the choice in every track without a front cover (FLAC, MP3,
Ogg, Opus and M4A) and saves it as `cover.jpg`. When every track has a
cover, it asks whether to keep it (Enter) or change it, with a link to the
embedded cover to look at. Every offered cover, and every album a
MusicBrainz search finds, comes with a link.

The cover step also works as a separate command, for any album of FLAC,
MP3, Ogg, Opus or M4A files:

```bash
cdport-cover ~/Temp/Album           # or ./cover.py ... in a checkout
cdport-cover -r ~/Music             # every album folder below ~/Music
cdport-cover ~/Temp/Album --release RELEASE_ID   # a MusicBrainz release ID
```

For each album with tracks lacking a picture, it offers covers right away;
an album whose tracks all have one can keep it (Enter) or change it, which
replaces the embedded front cover (other pictures stay) and `cover.jpg`.
Covers come from the Cover Art Archive: by the MusicBrainz release ID in the
tags (or `--release`), otherwise by searching MusicBrainz for the album and
artist tags and asking which hit is the right album. A picture file of your
own works too. Nothing is replaced unless you choose to change the cover.
MusicBrainz is asked at most once a second.

## Lyrics

In automatic mode (`lyrembed --auto`) cdport asks whether to romanize the
lyrics (Hangul, Kana, Hanzi to Latin letters). Enter keeps lyrembed's own
setting (`settings.auto_romanize`); `y`/`n` pass `--romanize`/`--no-romanize`.
After an automatic run cdport reads the tracks to see which ones still have
no lyrics (lyrembed skips a track without a confident match). It lists them
and offers lyrembed's interactive mode for them (Enter) or to continue without
them; instrumental tracks stay without lyrics either way. When every track
got lyrics, `l` opens the album in lyrembed's interactive mode to check them,
and Enter continues with the next step.

lyrembed's interactive mode is a page in the browser. On cdport's page the
tab goes there and comes back (see above). In the terminal lyrembed opens
its page with the album and cdport waits: quit lyrembed on the page, or close
the tab, and cdport continues with the next step.

lyrembed tags FLAC, MP3, Ogg, Opus and M4A; an album in other formats skips
the lyrics step.

## Into the library

At the end cdport offers to move the album to
`<library>/<album artist>/<album folder>`. It never overwrites an existing
folder, with one exception: the other disc of a multi-disc album. cyanrip
names every disc's folder after the album, so disc 2 finds disc 1's folder in
the library. When that folder holds the same release (MusicBrainz release ID,
else album and album artist) with other disc numbers, cdport offers to add the
disc to it (without release IDs, every track must have the same album title,
and the album artists must match or the discs share an artist). Its files are
named apart (`2.01 - …`, `<album> CD2.cue`); identical copies such as
`cover.jpg` are dropped, and any other file of the same name stops the merge.

The rip log (`*.log`, cyanrip's AccurateRip record) does not go into the
library: it is kept in `logs/` in the working folder, under a new name if an
older log has the same one. The cue sheet stays with the album. After the
summary cdport goes back to Start.

## Settings

`~/.config/cdport/config.json`, holding only the keys you want to change.
Unknown keys and wrong values are reported; those settings keep their
defaults, the others apply. [config.example.json](config.example.json)
lists every setting with its default and an explanation; a copy works as a
config as it is (the `_` keys are comments). The most common settings:

```json
{
  "work_dir": "",
  "library_dir": "",
  "library_layout": "{albumartist}/{folder}",
  "lyrics_mode": "ask",
  "editor": "eartag",
  "cyanrip": {
    "channel": "release",
    "path": "",
    "device": "",
    "offset": null,
    "mode": "fast",
    "deemphasis": true,
    "replaygain": true,
    "eject": true,
    "dir_scheme": "{album}{if #releasecomment# > #0# (|releasecomment|)}",
    "extra_args": []
  },
  "lyrembed": { "path": "", "extra_args": [] },
  "check_for_updates": true
}
```

- `deemphasis: true` (cyanrip's default) applies the deemphasis filter to
  discs flagged with preemphasis, so they sound right in players, which
  ignore the flag. `false` passes `-W` and rips them bit-exact (too bright
  unless filtered later).
- `replaygain: false` passes `-K`; `eject: false` drops `-Q`.
- `dir_scheme` is cyanrip's `-D` and must be relative. The default is
  cyanrip's own without the ` [FLAC]` suffix. Only the folder holding the
  tracks is kept: `{album_artist}/{album}` puts the album in
  `<working folder>/<album>`; `library_layout` decides the artist folder.
- Your own editors go under `"editors"`, each a list of command templates
  (the first one that exists is used). `{files}` as a whole argument expands
  to the track files; `{dir}` is the album folder:

  ```json
  { "editors": { "puddletag": { "name": "puddletag", "commands": [["puddletag", "{dir}"]] } },
    "editor": "puddletag" }
  ```

Ear Tag runs as a Flatpak and can only see `~/Downloads` and `~/Music`, so
its files are passed with `flatpak run --file-forwarding ... @@ FILES @@`.
Editors usually keep running after cdport starts them, and Ear Tag returns
right away when its window is already open. So cdport asks you to press
Enter once you have saved your edits.

## Working with the other programs

- **[lyrembed](https://github.com/sobakah/lyrembed)** embeds the lyrics in
  step 4. Its **Import a CD** starts cdport on its page with
  `--return-url`/`--return-name`: at the end, **Done – back to lyrembed**
  returns there with the imported album open.
- **Jewelcase** imports CDs and edits tags with cdport over D-Bus, also from
  inside a Flatpak.

### D-Bus

```bash
cdport --install-dbus-service
```

lets the session bus start cdport when a program calls it on
`io.github.sobakah.cdport`. That is how a music player such as Jewelcase can
import CDs with cdport, also from inside a Flatpak, which can neither start
host programs nor read cdport's settings (it needs
`--talk-name=io.github.sobakah.cdport`). The command writes
`~/.local/share/dbus-1/services/io.github.sobakah.cdport.service`, starting
the cdport that wrote it with `--dbus`; run it again after installing cdport
somewhere else. `--remove-dbus-service` removes it.

The interface (`io.github.sobakah.cdport1` on `/io/github/sobakah/cdport`):

| Call | What it does |
|---|---|
| `GetInfo() → a{sv}` | `Version`, `InterfaceVersion` (1), `LibraryDir`, `WorkDir`, `Editor` from the settings; `TagFormats` (`as`): the file extensions `EditTags` edits |
| `UnfinishedAlbums() → as` | albums in the working folder with steps still open |
| `Import(a{sv} options) → (s run, s url)` | starts a run on its own page (as `cdport --web --no-browser`) and answers with the page's address, for the caller to open; options `Library` (as `-l`) and `Folder` (one of `UnfinishedAlbums`, to continue it) |
| `EditTags(s folder, a{sv} options) → (s run, s url)` | cdport's tag editor on one album, on its own page (as `cdport FOLDER --edit-tags --web --no-browser`); the folder must hold files `TagFormats` names (FLAC, MP3, Ogg, Opus, M4A) and lie inside cdport's library, its working folder or the music folder; no options yet |
| `Runs() → a(ss)` | the runs still going, with their addresses |
| `Stop(s run)` | as Ctrl+C: a running rip finishes first (a second Stop changes nothing) |
| signal `Finished(s run, i exit_code, s album)` | a run ended; `album` is the folder it finished (after `EditTags`: the album's folder, renamed if its name changed), or empty |

It has no call that runs a command or works on any folder the caller names
(an import continues only an album `UnfinishedAlbums` lists, the tag editor
opens only inside the library, working or music folder), so it gives a
sandboxed program no way out. The tag editor writes only what you save on
its page. A run whose page nobody opens within 10 minutes ends. Without calls
or runs for a minute, the service ends (never while a run is going); the bus
starts it again at the next call.

```bash
gdbus call --session -d io.github.sobakah.cdport -o /io/github/sobakah/cdport \
  -m io.github.sobakah.cdport1.Import "{'Library': <'$HOME/Music'>}"
gdbus call --session -d io.github.sobakah.cdport -o /io/github/sobakah/cdport \
  -m io.github.sobakah.cdport1.EditTags "$HOME/Music/Artist/Album" "{}"
```

## Limits

- Linux only for now; Windows is planned.
- cyanrip's distribution packages (0.9.3.1 and older) cannot rip a disc
  MusicBrainz does not know with a release's tags; type the tags in, or use
  `"channel": "master"`.

## License

MIT, see [LICENSE](LICENSE).

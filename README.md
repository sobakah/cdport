# cdimport

Interactive terminal program that turns an audio CD into a tagged,
lyrics-embedded FLAC album. It is glue: it drives existing tools in order and
does not rip, tag or fetch lyrics itself.

```
0. Tools    cyanrip, a tag editor, lyrembed: found or not; offers to install cyanrip
1. Disc     wait for an audio CD, preview the MusicBrainz match (cyanrip -I)
2. Rip      cyanrip → FLAC with tags, cover, AccurateRip log
3. Tags     Ear Tag (or Kid3, Picard, ...) for manual revision, then a tag check
4. Lyrics   lyrembed, automatic or interactive
5. Finish   offer to move the album into the music library, summary
```

Version 0.4.2 · Python 3.10+ · Linux (Windows later)

## Requirements

- [cyanrip](https://github.com/cyanreg/cyanrip) for ripping. Without it,
  cdimport still continues albums ripped earlier.
- A tag editor: [Ear Tag](https://flathub.org/apps/app.drey.EarTag) by
  default, Kid3 or Picard as alternatives, or none.
- [lyrembed](https://github.com/sobakah/lyrembed) for lyrics (optional).
- `mutagen` for the tag check after editing (installed with cdimport).

When cyanrip is missing, cdimport offers to install it. It shows every
command first and asks before running it. Where the distribution has a
package, it uses the package manager (`sudo apt install cyanrip`, ...). On
Fedora and other systems without one, it builds cyanrip from source:

1. It checks the build dependencies with pkg-config and, on Fedora, offers
   `sudo dnf install ...` for the missing ones.
2. It asks for the clone folder and the meson build folder. Both prompts
   are prefilled and editable (Tab completes paths): the folders remembered
   from the last build, else `~/.local/src/cyanrip` and its `build`
   subfolder.
3. It clones the repository, then builds and installs with meson into
   `~/.local` (no sudo).
4. It saves both folders as `cyanrip_source_dir` and `cyanrip_build_dir` in
   the config.

Which version is controlled by `cyanrip.channel` in the config:

- `"release"` (default): the latest release, from the distribution package
  or as a source build of the newest `vX.Y.Z` tag.
- `"master"`: the `master` branch, always built from source into
  `~/.local`, also where a package exists. Use it when the release does not
  build against your FFmpeg: v0.9.3.1 does not compile against FFmpeg 9
  (Fedora 45).

At start cdimport compares the installed cyanrip (`cyanrip -v`) with GitHub,
which it asks at most once a day:

- A release installed with `"master"` set: it offers to build master
  (on every start, since the config asks for it).
- An older release than the latest, or a master build behind `master`: it offers
  to rebuild a cyanrip in `~/.local`. For a distribution package it only
  tells you; the package manager does the update. Shown at most once a day.

`cdimport --install-cyanrip` updates and rebuilds a source build (or
reinstalls the package). For a missing editor or lyrembed, cdimport only
shows how to install it.

## Usage

```bash
pipx install .            # or run ./run.py from the checkout
cdimport                  # rip the disc in the drive
cdimport ~/Temp/Album     # continue with an album ripped earlier
cdimport --lyrics auto --editor skip
cdimport --install-cyanrip    # update and rebuild cyanrip first
cdimport --rip-mode secure      # paranoia for every track (default: fast)
```

On first use cdimport asks for two folders and saves them in the config: the
working folder, where rips land first (suggested: `~/Temp`), and the music
library (suggested: your desktop's music folder, e.g. `~/Music` or
`~/Musik`). `-w DIR` and `-l DIR` use other folders for one run.

### Fast and secure rips

By default cdimport rips **fast**: cyanrip reads without paranoia
(`-P none`), and afterwards every track is checked against the AccurateRip
database. Tracks it does not confirm are ripped again with full paranoia
(`-P max`) and replace the fast ones; that log is kept as
`<album> (secure re-rip).log`. A disc that is not in the AccurateRip
database cannot be checked, so it is ripped securely right away.

**Secure** mode uses cyanrip's paranoia for every track: each block is read
in overlapping, verified pieces, and the drive's cache is defeated by
seeking away and back. On the maintainer's slim USB drive that took about
twice as long (200 s instead of 113 s for a 3:48 track, both confirmed by
AccurateRip). Set `cyanrip.mode` to `"secure"`, pass `--rip-mode secure`
for one run, or press `p` in the disc menu for one disc.

### Several releases for one disc

When several MusicBrainz releases have the disc's ID (editions, reissues),
cyanrip lists them and waits for a choice. cdimport shows them as numbered
options with a link to each release page; pick one to see its tags, then rip.

### Discs MusicBrainz does not know

cyanrip looks the disc up by its disc ID. When MusicBrainz has no release with
that ID, cyanrip will not rip without tags, and cdimport offers:

- `s` search MusicBrainz for the release by album and artist. Each hit shows
  its media and track counts (the disc's count is marked) and a link.
- `m` a release ID or a pasted MusicBrainz release link. cyanrip takes the
  tags from that release even though the disc ID is not attached to it.
- `t` type in the tags: album, album artist, year, track titles (cyanrip's
  `-a`/`-t`). The tag step can refine them.
- `n` rip without MusicBrainz metadata.

It also shows cyanrip's link for adding the disc ID to MusicBrainz (needs a
MusicBrainz account). Open it while logged in, search for the release, select
the edition with the same number of tracks and attach the disc ID; if the
release is missing, add it there. Enter an edit note and submit. Once the edit
is applied (it may wait up to 7 days for votes), cyanrip finds the disc by
itself, for you and everyone else.

If the tracks have no embedded cover (cyanrip only takes an image the Cover
Art Archive marks as front cover), cdimport offers what the archive has for
the release or another edition of the album, or a picture file of your own.
It embeds the choice in every FLAC without a picture and saves it as
`cover.jpg`. When every track has a cover, it asks whether to keep it
(Enter) or change it, with a link to the embedded cover to look at. Every
offered cover, and every album a MusicBrainz search finds, comes with a link.

Every album folder in the working folder gets a
small `.cdimport.json` recording the finished steps. When a run is stopped
(Ctrl+C, or `q` at a prompt), the next run offers to continue it. The file is
deleted when the album is finished.

At the end cdimport offers to move the album to
`<library>/<album artist>/<album folder>`. It never overwrites an existing
folder. The one exception to "never merges": the other disc of a multi-disc
album. cyanrip names every disc's folder after the album, so disc 2 finds
disc 1's folder in the library. When that folder holds the same release
(MusicBrainz release ID, else album and album artist) with other disc
numbers, cdimport offers to add the disc to it. Its files are named apart
(`2.01 - …`, `<album> CD2.cue`); identical copies such as `cover.jpg` are
dropped, and any other file of the same name stops the merge. The rip log (`*.log`, cyanrip's AccurateRip record) does
not go into the library: it is kept in `logs/` in the working folder, under a
new name if an older log has the same one. The cue sheet stays with the
album. After the summary cdimport asks whether to start another run (next
disc or album); Enter ends it.

In automatic lyrics mode cdimport asks whether to romanize the lyrics
(Hangul, Kana, Hanzi to Latin letters). Enter keeps lyrembed's own setting
(`settings.auto_romanize`); `y`/`n` pass `--romanize`/`--no-romanize`. This
needs lyrembed 1.0.1 or newer; older versions are not asked. After an
automatic run cdimport offers to check the result: `l` opens the album in
lyrembed's interactive mode, which shows every track, including the ones that
just got lyrics. Enter continues with the next step.

## Cover art on its own: cdimport-cover

The cover step also works as a separate command, for any FLAC album:

```bash
cdimport-cover ~/Temp/Album           # or ./cover.py ... in a checkout
cdimport-cover -r ~/Musik             # every album folder below ~/Musik
cdimport-cover ~/Temp/Album --release 5c580fb1-7c31-4785-afde-68098dc5caa2
```

For each album with tracks lacking a picture, it offers covers right away;
an album whose tracks all have one can keep it (Enter) or change it, which
replaces the embedded front cover (other pictures stay) and `cover.jpg`.
Covers come from the Cover Art Archive: by the MusicBrainz release ID in the tags (or
`--release`), otherwise by searching MusicBrainz for the album and artist
tags and asking which hit is the right album. A picture file of your own
works too. Nothing is replaced unless you choose to change the cover.
MusicBrainz is asked at most once a second.

## Configuration

`~/.config/cdimport/config.json`, holding only the keys you want to change.
Unknown keys or wrong types are reported. [config.example.json](config.example.json)
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
    "deemphasis": true,
    "replaygain": true,
    "eject": true,
    "dir_scheme": "{album}{if #releasecomment# > #0# (|releasecomment|)}",
    "extra_args": []
  },
  "lyrembed": { "path": "", "extra_args": [] }
}
```

- `deemphasis: true` (cyanrip's default) applies the deemphasis filter to
  discs flagged with preemphasis, so they sound right in players, which
  ignore the flag. `false` passes `-W` and rips them bit-exact (too bright
  unless filtered later).
- `replaygain: false` passes `-K`; `eject: false` drops `-Q`.
- `dir_scheme` is cyanrip's `-D` and must be relative. The default is
  cyanrip's own without the ` [FLAC]` suffix.
- Your own editors go under `"editors"`, each a list of command templates
  (the first one that exists is used). `{files}` as a whole argument expands
  to the track files; `{dir}` is the album folder:

  ```json
  { "editors": { "puddletag": { "name": "puddletag", "commands": [["puddletag", "{dir}"]] } },
    "editor": "puddletag" }
  ```

Ear Tag runs as a Flatpak and can only see `~/Downloads` and `~/Music`, so
its files are passed with `flatpak run --file-forwarding ... @@ FILES @@`.
Editors usually keep running after cdimport starts them, and Ear Tag returns
right away when its window is already open. So cdimport asks you to press
Enter once you have saved your edits.

## Development

```bash
pip install -e ".[dev]"
pytest
pyflakes cdimport tests
```

The tests never touch the network, the drive or the real tools.

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

Version 0.1.0 · Python 3.10+ · Linux (Windows later)

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
```

On first use cdimport asks for two folders and saves them in the config: the
working folder, where rips land first (suggested: `~/Temp`), and the music
library (suggested: your desktop's music folder, e.g. `~/Music` or
`~/Musik`). `-w DIR` and `-l DIR` use other folders for one run.

If the tracks have no embedded cover (cyanrip only takes an image the Cover
Art Archive marks as front cover), cdimport offers what the archive has for
the release or another edition of the album, or a picture file of your own.
It embeds the choice in every FLAC without a picture and saves it as
`cover.jpg`. When every track has a cover, it asks whether to keep it
(Enter) or change it.

Every album folder in the working folder gets a
small `.cdimport.json` recording the finished steps. When a run is stopped
(Ctrl+C, or `q` at a prompt), the next run offers to continue it. The file is
deleted when the album is finished.

At the end cdimport offers to move the album to
`<library>/<album artist>/<album folder>`. It never overwrites or merges into
an existing folder. The rip log (`*.log`, cyanrip's AccurateRip record) does
not go into the library: it is kept in `logs/` in the working folder, under a
new name if an older log has the same one. The cue sheet stays with the
album.

In automatic lyrics mode cdimport asks whether to romanize the lyrics
(Hangul, Kana, Hanzi to Latin letters). Enter keeps lyrembed's own setting
(`settings.auto_romanize`); `y`/`n` pass `--romanize`/`--no-romanize`. This
needs lyrembed 1.0.1 or newer; older versions are not asked.

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

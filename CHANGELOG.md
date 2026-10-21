# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.6.1] — 2026-10-21

### Added

* **The tag editor shows every tag** in the files: disc and track number
  to edit, and all other tags (MusicBrainz IDs and the like) under "More
  tags", once for the album when every track has the same value, else per
  track. They can be changed or removed; lyrics stay lyrembed's.
* **The tag editor shows the embedded cover**, once for the album or per
  track when they differ. **Change the cover…** saves, goes back to the
  cover step and returns to the editor.

### Changed

* **The page scrolls like a terminal**: it follows new output and
  questions only while you are at the end, and stays where you are when
  you have scrolled up. The same inside cyanrip's output box.

## [0.6.0] — 2026-10-21

### Added

* **A page in the browser** (`--web`, preview): the terminal's steps,
  unchanged, with a step bar, the messages, cyanrip's output live (and a
  Stop button that ends it like Ctrl+C) and each question as buttons or a
  field. The run starts when the page opens; closing the tab stops it at
  the next question, never during a rip. Only this computer can open the
  page, with the token in the printed address.
* **Pictures with the choices** on the page: releases, albums and cover
  candidates show their cover, "keep or change" the embedded one.
* **A tag editor** on the page, offered first in the tags step: album,
  album artist, date, genre, each track's title and artist; several
  artists as Picard writes them (`ARTIST` the credit, `ARTISTS` the
  names); "(feat. X)" moved from titles to the artists; search and
  replace in titles. File names follow titles and the folder the album
  where cyanrip built them; the cue sheet follows, the rip log stays
  untouched; nothing is written unless all of it can be.
* **The lyrics step hands the tab to lyrembed's page** (lyrembed 2.1.0)
  and gets it back with "Done – back to cdimport"; lyrembed's output,
  also in automatic mode, shows on the page.
* **`--return-url URL --return-name NAME`** with `--web`: the finished
  run's button leads back there. lyrembed's "Import a CD" uses it, and
  opens the imported album afterwards.

## [0.5.1] — 2026-10-21

### Changed

* **Ready for lyrembed 2.0**, whose interactive mode is a page in the
  browser instead of terminal menus. cdimport tells the versions apart by
  `lyrembed --version`. With 2.0 its choices say that the album opens on
  lyrembed's page, and before it opens cdimport says how to get back:
  quit lyrembed on the page, or close the tab, and cdimport continues with
  the next step. Older lyrembed versions work and read as before.

## [0.5.0] — 2026-10-15

### Fixed

* **Automatic lyrics that found nothing:** `lyrembed --auto` exits 0 also
  when it embedded nothing (a track without a confident match counts as
  skipped there), so cdimport marked the step done and its "go through
  them interactively" offer never appeared. cdimport now reads the tracks
  afterwards, lists those still without lyrics and offers lyrembed's
  interactive mode for them.
* **Offset "0 for this rip only"** stayed for every later disc of the same
  session; the next disc asks again.
* **A typo in the config** threw the whole file away, so cdimport asked
  for the folders again and forgot the drive offset. Only the broken
  settings fall back to their defaults now. `"offset": true` and an
  editor whose `name` is not text are reported instead of accepted.
* **Links** included a following `;`, `.` or `,`, so the AccurateRip drive
  list link opened a missing page.
* **Secure re-rip:** files that do not belong to the album are reported, as
  after the first rip, instead of failing after tracks were replaced; when
  the re-rip fails, the disc is still ejected.
* **Cover offer:** a track with only a back cover (or another picture) now
  counts as having no cover. The preview of the embedded cover is written
  to the per-user `$XDG_RUNTIME_DIR/cdimport/` and deleted after the
  question, not to a fixed name in `/tmp`.
* **Tool output without a terminal** (output piped, e.g. into `tee`) is
  captured through a pipe, so the disc preview still finds the releases,
  the AccurateRip status and the MusicBrainz link, and the offset
  measurement its result. After Ctrl+C the tool's output is still read
  while it cleans up, so it cannot block on a full terminal buffer.
* **Multi-disc merge:** without a MusicBrainz release ID, all tracks of
  both discs are compared (album title, album artist or an artist in
  common), not only track 1, whose artist differs on albums with guests. A
  merge that fails halfway moves the files back.
* **lyrembed install hint** pointed to a folder on the maintainer's disk;
  it is `pipx install git+https://github.com/sobakah/lyrembed.git` now.
* `flatpak run --arch x86_64 APP` editor commands: the app is found.
* Albums in formats lyrembed does not tag (WAV, AIFF, WavPack, TTA) skip
  the lyrics step with an explanation instead of "lyrembed failed". The tag
  check mentions lyrembed's `prefer_album_artist` for tracks with an album
  artist but no artist.

## [0.4.2] — 2026-10-14

### Fixed

* **Multi-disc albums:** the second disc of an album could not be moved to
  the library, because cyanrip names every disc's folder after the album
  and disc 1's folder was already there. cdimport now offers to add the
  disc to that folder when it holds the same release with other disc
  numbers. Nothing there is replaced: byte-identical files (`cover.jpg`)
  are dropped, any other file of the same name stops the merge.
* **Eject after a fast rip:** the disc stayed in after the first fast rip.
  cdimport ejected it with one `CDROMEJECT` call, which the kernel refuses
  while udev or udisks still look at the drive; the reason was only a dimmed
  line. It now retries while the drive is busy, falls back to util-linux's
  `eject` (which also tries a SCSI command, like cyanrip's libcdio), and
  warns visibly if the disc still stays in.

## [0.4.1] — 2026-10-14

### Added

* **Fast rips by default:** cyanrip reads without paranoia, every track is
  checked against AccurateRip, and tracks it does not confirm are ripped
  again securely (`-P max`) in place of the fast ones. Discs not in the
  AccurateRip database are ripped securely. About half the time of a secure
  rip on a slim USB drive. `cyanrip.mode` (`"fast"`/`"secure"`),
  `--rip-mode` for one run, `p` in the disc menu for one disc.

### Fixed

* **Parallel runs:** a cdimport working with a disc (preview, offset
  measurement, rip) now locks the drive. Another cdimport leaves a locked
  drive alone instead of running `cyanrip -I` on it during a rip, and offers
  to wait until it is free. The lock is per user
  (`$XDG_RUNTIME_DIR/cdimport/drive-sr0.lock`) and ends with the process.

## [0.4.0] — 2026-10-12

### Changed

* **Easier-to-read output:** messages start with ✓, ! or ✗; menus show keys
  in colour with links dimmed and aligned under their option; links are
  underlined and clickable in terminals that support it; the tool check and
  the summary are aligned checklists with paths shortened to `~`; cyanrip's,
  lyrembed's and build tools' output is framed (`╭─ cyanrip` … `╰─ done`);
  blocks are separated by single blank lines. Without colour (`--color
  never`, `NO_COLOR`) only the marks and the layout remain.

## [0.3.0] — 2026-10-12

### Added

* **Discs MusicBrainz does not know:** search MusicBrainz for the release
  (rips with its tags via `-R`, even before the disc ID is attached), paste
  a release link, or type in album, artist, year and track titles (cyanrip's
  `-a`/`-t`). The link for adding the disc ID to MusicBrainz is shown with
  the steps, and again after the rip.
* **Several releases for one disc:** the releases cyanrip lists are offered
  as numbered options with links; before, only `m` with the list number
  worked and `r` failed.

## [0.2.0] — 2026-10-11

### Added

* **Another run:** after the summary cdimport asks whether to start another
  run (next disc or album) instead of ending.
* **Check automatic lyrics:** after `lyrembed --auto`, cdimport offers to
  open the album in interactive lyrembed to look over what was embedded.
* **Cover links everywhere:** the keep-or-change question links the
  embedded cover (written to a temporary preview file), and each album in a
  MusicBrainz search links its MusicBrainz page. Offered covers already had
  links.

## [0.1.0] — 2026-10-11

First pre-release. Not yet tested end to end against a real CD from start
to finish; the individual steps were tried on real hardware.

### Added

* **Steps 0–5:** find the tools, wait for an audio CD and preview the
  MusicBrainz match, rip with cyanrip, revise tags in Ear Tag (or Kid3,
  Picard, any command template), embed lyrics with lyrembed (automatic or
  interactive), and offer to move the album into the music library.
* **Resumable:** a `.cdimport.json` per album records the finished steps;
  Ctrl+C or `q` leaves a state the next run offers to continue.
* **Installing cyanrip:** package manager where a package exists, otherwise
  a meson source build into `~/.local` with prefilled, editable clone and
  build folders. `cyanrip.channel` picks `release` or `master`; an update
  check at start (at most once a day) offers rebuilds.
* **Drive offset:** measured with `cyanrip -f` and saved automatically from
  a confidence of 4.
* **Cover art:** offered when tracks lack a front cover (Cover Art Archive,
  another edition of the album, or a file), and whenever an album is opened
  again, where an existing cover can be changed. Also a separate command,
  `cdimport-cover`.
* **Rip logs stay out of the library:** they are kept in `<work_dir>/logs/`.
* **Romanization question** in automatic lyrics mode (needs lyrembed 1.0.1).
* Working folder and music library are asked for on first use and saved in
  `~/.config/cdimport/config.json`; `config.example.json` explains every
  setting.

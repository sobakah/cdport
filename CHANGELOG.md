# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

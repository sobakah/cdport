# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

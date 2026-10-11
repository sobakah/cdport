# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

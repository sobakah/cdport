# Changelog

All notable changes to cdport are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.1] — 2026-11-14

### Added

- **Fedora Silverblue, Bluefin and other image-based systems:** cdport runs in a toolbox or
  distrobox, where it builds cyanrip. Its app menu entry and D-Bus service start it in the
  container, so Jewelcase on the host reaches it; the page opens in the host's browser, and
  Ear Tag and the other Flatpak editors are the host's. On the host itself, the cyanrip step
  says to use a toolbox instead of offering dnf. The README has a section for these systems.
- In a container, a lyrembed installed only on the host is reported as such, not as a broken
  pipx install.

## [1.0.0] — 2026-11-11

The first release. cdport rips an audio CD into a tagged, lyrics-embedded FLAC
album by driving cyanrip, a tag editor and lyrembed in order, in the terminal
or on a page in the browser. It starts at a Start screen that offers the disc
in the drive, unfinished albums or any album folder, and comes back there
after each album.

### Features

- **Install with pipx:** `pipx install git+https://github.com/sobakah/cdport.git`;
  `--install-desktop-entry` adds its page to the app menu, and cdport looks
  for its own updates at every start and installs them on request.
- **cyanrip found or installed:** from the distribution's package manager, or
  built from source into `~/.local` with the build dependencies installed
  through `dnf`, `apt`, `pacman` or `zypper`; `cyanrip.channel` picks the
  latest release or `master`, and cdport offers updates.
- **Disc preview and MusicBrainz:** the match is shown before ripping; several
  releases for one disc are offered with links, and for discs MusicBrainz
  does not know cdport searches for the release, takes a release link, or
  lets you type the tags in, and shows the link for adding the disc ID.
- **Fast rips checked against AccurateRip:** cyanrip reads without paranoia,
  and tracks AccurateRip does not confirm are ripped again securely;
  `secure` mode uses paranoia for every track. The drive offset is measured
  and saved, and a drive in use is locked against a second cdport.
- **Correct loudness tags:** cyanrip's R128 gain tags, 10 dB off, are deleted
  after each rip; its ReplayGain tags stay.
- **Covers:** offered whenever an album is opened, from the Cover Art Archive
  (also another edition) or a file, embedded in FLAC, MP3, Ogg, Opus and M4A
  tracks and saved as `cover.jpg`; also as the separate `cdport-cover`
  command.
- **A tag editor on the page** for FLAC, MP3, Ogg Vorbis, Opus and M4A: album
  and track fields, every other text tag under Picard's names, several
  artists as Picard writes them, the embedded cover, and file and folder
  names that follow the tags. Ear Tag, Kid3, Picard or your own editor in
  the terminal.
- **Lyrics with lyrembed:** automatic (with an optional romanization) or
  interactive on lyrembed's page, which the tab hands over to and comes back
  from; tracks left without lyrics are listed and offered interactively.
- **Go to any step:** on the page Tags, Lyrics, Finish and Cover can be chosen
  for the open album in any order; `--step` and `--edit-tags` do the same
  from the command line. Stopped runs are resumed where they ended.
- **Into the library:** the album is offered a place in the music library,
  never overwriting anything; the second disc of an album joins the first
  one's folder, and rip logs stay in the working folder.
- **A D-Bus interface** (`io.github.sobakah.cdport`) for Jewelcase and other
  programs, also from inside a Flatpak: import a CD or edit an album's tags
  on a page of its own, list unfinished albums, stop a run, hear when it
  ended.
- **Works with lyrembed's Import a CD**, which starts cdport and gets the
  imported album back.
- **Settings** in `~/.config/cdport/config.json`, every one explained in
  `config.example.json`; a wrong value falls back to its default and is
  reported.

[Unreleased]: https://github.com/sobakah/cdport/compare/1.0.1...HEAD
[1.0.1]: https://github.com/sobakah/cdport/compare/1.0.0...1.0.1
[1.0.0]: https://github.com/sobakah/cdport/releases/tag/1.0.0

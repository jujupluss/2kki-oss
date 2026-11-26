# Yume 2kki — open-source remake workspace

Tooling to work on **Yume 2kki** (RPG Maker 2000) with the open-source
[EasyRPG Player](https://github.com/EasyRPG/Player) and
[liblcf](https://github.com/EasyRPG/liblcf). It converts the game's binary
maps and database to XML, so they can be diffed, edited and scripted, and
rebuilds a playable game from that XML.

This repository holds **tooling only**. The game itself is copyrighted and
never committed: everyone supplies their own copy.

## Setup

Requirements: Python ≥ 3.10, GNU `patch`, CMake ≥ 3.22, Ninja, a C++17
compiler, and EasyRPG Player's libraries (SDL2, pixman, libpng, zlib, fmt,
freetype, harfbuzz, ICU, expat, inih; the Debian/Ubuntu `-dev` packages work).

```sh
git clone --recursive <this repo>
cd <repo>
# put the game under original/:
#   original/Yume2kki_ver0.129h/          base game
#   original/Yume2kki_ver0.129h_patch1/   patch (any folder with "patch" in its name)
make extract      # build lcf2xml, fix filenames, create build/game + xml/
make prune-tree   # remove dead map-tree entries (once, after the first extract)
make run          # build EasyRPG Player and play build/game
```

## Everyday use

```sh
$EDITOR xml/Map0001.emu   # edit a map (or RPG_RT.edb, the database)
make data                 # rebuild only what you edited into build/game
make run
```

| command | what it does |
|---|---|
| `make verify` | sanity-check the extracted game |
| `make audit` | find missing / unused assets (`notes/assets.md`) |
| `make fetch-yno` | fetch the community English translation (see below) |
| `make translate` / `make run-en` | build and play the English translation |
| `make content-status` / `content-export` | save your edits as diffs in `content/` |
| `make content-apply` | apply someone else's `content/` to your `xml/` |
| `make test` | run the test suite (no game data needed) |
| `make help` | list every target |

After running `make extract` again, run `make data`: your `xml/` edits are
kept, and the next `make data` rebuilds them (about a minute).

## English

```sh
make fetch-yno && make run-en
```

This plays the game with the community English translation used by the
browser version ([YNOproject](https://github.com/ynoproject/ynotranslations)),
including its translated images. It is fetched into `external/` and never
committed: that repository has no licence and asks not to re-upload its
translations. Coverage, credits and Yume 2kki's translation guidelines are in
`notes/translation.md`.

## Good to know

- **`original/` is read-only.** Images, music and sounds in `build/game` are
  hardlinks into it to save ~7 GiB, so don't edit them in place. Put new
  assets in `content/assets/`, or extract with
  `python3 scripts/extract.py --copy`.
- **Filenames:** the archive was unpacked with the wrong codepage, so
  Japanese names in `original/` look garbled (`ВфВ▀2В┴Вл` = `ゆめ2っき`).
  `make extract` fixes them; see `notes/encoding.md`.
- **RTP:** the game expects the RPG Maker 2000 RTP for default sounds and
  battle graphics. Install it, or pass `--rtp-path` / set `RPG2K_RTP_PATH`.
  It runs without it, minus those sounds.

## Documentation

| file | topic |
|---|---|
| `content/README.md` | sharing your own maps and events as diffs |
| `notes/translation.md` | translation pipeline and coverage |
| `notes/roundtrip.md` | LCF ⇄ XML fidelity |
| `notes/map-tree.md` | map-tree cleanup |
| `notes/assets.md`, `notes/audit.md` | asset and content audits |
| `notes/encoding.md` | the filename encoding problem |

## Licensing

- This repository's scripts, docs and tests: MIT (`LICENSE`).
- EasyRPG Player (GPLv3) and liblcf (MIT) are git submodules under `vendor/`.
- Yume 2kki's assets, maps and text belong to their creators; the game's
  `read_me.txt` and `document/copyright.txt` forbid redistribution without
  permission. That is why `original/`, `build/` and `xml/` are gitignored.
  A redistributable game would need all-original content.

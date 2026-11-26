# Translation pipeline (English)

Tool: `scripts/translate.py`. Commands:

```sh
make fetch-yno   # optional: fetch YNOproject's community translation
make translate   # generate translation/en/*.po (+ build/translation-report.md)
make run-en      # play with --language-path translation --language en
```

`translation/en/` is gitignored (it contains the game's source text). Only the
curated `translation/glossary.json` and `translation/overrides.json` are
committed. The fetched community translation lives in `external/` and is
gitignored too (see below).

## How EasyRPG Player consumes it

Verified in `vendor/Player/src/translation.cpp`:

- `language/<lang>/meta.ini` — `[Language] Name/Code/Description/GameTitle`.
- `rpg_rt.ldb.po` — database strings. Lookup order for a field:
  `actors.1.name` (indexed), then `actors.name`, then context-free `""`.
- `rpg_rt.ldb.common.po` — common-event message boxes.
- `rpg_rt.ldb.battle.po` — troop message boxes (empty in this game).
- `rpg_rt.lmt.po` — map-tree names.
- `Map####.po` — message boxes for one map, loaded when the map loads.

A message `msgid` is the **whole message box**: consecutive `ShowMessage`
(`10110`) + `ShowMessage_2` (`20110`) lines joined with `\n`. A choice `msgid`
is the `ShowChoiceOption` (`20140`) list joined with `\n`. This matches
`Translation::RewriteEventCommandMessage` exactly, including the
`actors.name`, `actors.title` and `strpic` special contexts.

## Importing the community translation (YNOproject)

The browser version of Yume 2kki (YNOproject) uses the community English
translation in [ynoproject/ynotranslations](https://github.com/ynoproject/ynotranslations)
(`2kki/en`). It is in the same EasyRPG format this pipeline writes, so
`make fetch-yno` makes a shallow, sparse clone of just `2kki/<lang>` into
`external/ynotranslations/`, and `make translate` imports it automatically
(`--import`).

How the import works (`scripts/translate.py`):

- entries match by context + exact `msgid`: first in the same catalogue
  file, then anywhere in the set (text that moved between maps or into a
  common event). Database contexts are normalised (`actors.1.name` ->
  `actors.name`). Changed text is never guessed at.
- by default an imported translation **wins**, and `glossary.json` /
  `overrides.json` fill the gaps, so names and terms match the browser
  version throughout. `--prefer local` flips this. With the current data, 53
  of our entries differ from YNO's and are overridden: 29 glossary entries
  (e.g. `うろつき` "Wanderer" -> "Urotsuki") and all 24 `Map0002` overrides.
- translated images (`Picture/`, `Panorama/`, `Title/`) are copied into
  `translation/en/`. Player checks the language folder before the game folder
  for every asset (`find_generic` in `vendor/Player/src/filefinder.cpp`).
- the import's `meta.ini` `Description` (its credit line) is kept.

**Licensing:** ynotranslations has no licence file, and its README says "Do
not upload translations, assets from translations or assets you do not have
the right for." So its text and images are never committed here: `external/`
and `translation/*/` are gitignored, and each contributor fetches it
themselves, just as they supply their own copy of the game.

## Current coverage

Measured on 0.129h + patch1 with ynotranslations `357563d` (2026-11-17):

| | entries |
|---|---:|
| all catalogue entries | 35667 |
| player-facing entries containing Japanese | **7999** |
| ... translated | **1648 (20.6%)** |
| ... untranslated, and also untranslated in YNO's files | 3928 |
| ... untranslated, not in YNO's files at all | 2423 |
| editor-only names, not counted (map tree, chipset/animation/terrain) | 3212 |
| YNO translations unused (text not in this game version) | 6 of 1453 |
| translated images imported | 336 |

What the numbers mean:

- **We match the browser version's text.** 1447 of YNO's 1453 translations
  are used (159 of them found in a different file than in YNO's set); the 6
  unused ones are text that differs in the newer game version YNO targets.
- **Most remaining Japanese is Japanese on the browser version too.** YNO's
  own files contain 9413 entries, only 1453 of them translated. Many of the
  rest are developer-room notes and debug text (e.g. `Map0191`), or hero
  names used as hidden string variables (`actors.name` contexts from
  `ChangeHeroName`).
- **Some text must stay in Japanese.** Notably the wallpaper gallery
  (`Map0899`) per the translation guidelines below; the report flags it.
- **Editor-only names** (map tree, chipsets, animations, terrains) are never
  shown by RPG Maker 2000. They are still written but not counted, and YNO
  does not translate them either.
- Every generated catalogue (and YNO's) parses with Player's own
  `Dictionary::FromPo`: a test harness linked against the Player build parsed
  all 3934 files without error, and a deliberately broken file failed.

`build/translation-report.md` lists the biggest remaining gaps per file.

## Translation guidelines (Yume 2kki)

YNO's README links Yume 2kki's own rules, which "must be respected":
[wikiwiki.jp/yumenikki-g3, 決定事項 > 翻訳ガイドライン](https://wikiwiki.jp/yumenikki-g3/%E6%B1%BA%E5%AE%9A%E4%BA%8B%E9%A0%85#TranslationGuidelines).
Summarised from that page; read the original before publishing anything
(this summary went through an automated fetch and is not verbatim):

- a translation must say Yume 2kki is a Yume Nikki fan game that Kikiyama is
  not involved in, and credit the original staff and the translators;
- it may not be sold or used for profit; the original staff may require
  changes to its content or distribution;
- must translate: `ReadMe.txt` and the prologue shown at new game;
- must **not** translate: illustration titles and artist names (e.g. the
  `Map0899` wallpaper gallery) and staff names in the end credits;
- anything else follows each map's / material's terms
  (`各マップ、素材の利用規約.txt`) or needs the creator's permission;
- only translation and its publication are permitted; no added content.

## Credits

English translation of Yume 2kki by the YNOproject translators, as credited
in the ynotranslations README: aku, Carbonara, FlashfyreDev (Sam), Fonvight,
IStoleThePies, Jerichi, Kong, Midomido, Nulsdodage (Wavyup) and
YouArentDistorted (yugamineena).

## How to add translations

Two mechanisms in `translation/`:

1. `glossary.json`
   ```json
   {
     "meta": {...},
     "terms":  {"gold": "Dreams"},
     "strings": {"はい": "Yes"}
   }
   ```
   `terms` is keyed by `Terms` field name. `strings` is keyed by exact source
   text (surrounding spaces/ideographic spaces are ignored). Multi-line
   message boxes are translated line by line when every line matches.

2. `overrides.json`
   ```json
   {"messages": {"<exact msgid>": "<exact msgstr>"}}
   ```
   For control-code-heavy menus where line-by-line lookup is not enough.
   `scripts/translate.py` keys these by the exact generated `msgid`.

After editing either file run `make translate` again. Do not hand-edit
`translation/en/*.po`; it is regenerated. When YNO's translation is imported
it wins over these files unless you pass `--prefer local` (run
`scripts/translate.py` directly, or edit the `translate` target).

## Caveats

- RPG Maker control codes (`\>`, `\C[n]`, `\V[n]`, `\N[n]`, `\.`, `\|`, `\^`)
  must be preserved in `msgstr`. The real-room overrides do this.
- Raw CR (`\r`): after the CR fix (`notes/roundtrip.md`) the XML keeps CR as
  `&#xD;`. In 0.129h+patch1 every one of them (145 commands in 28 maps) is in
  an editor comment, event code `12410`/`22410`, never in a message or
  choice, so no generated `.po` contains a CR
  (`grep -l $'\r' translation/en/*.po` finds nothing). That matters because
  Player's `Utils::ReadLine` (`vendor/Player/src/utils.cpp`) treats a bare CR
  as a line break and its PO parser has no `\r` escape: a CR inside a
  `msgid` would end the line early and make `Dictionary::FromPo` raise a
  fatal "Unterminated line" error.
- Player's PO parser only understands `\\`, `\n` and `\"` escapes; the writer
  emits exactly those.
- Map POs are per-map. A phrase translated in `glossary.json` appears in every
  map where it occurs; a whole-message override applies to every map that
  contains the same box.

## Suggested next pass

1. Keep `make fetch-yno` up to date; YNO's set is actively maintained.
2. Translations for what YNO lacks belong upstream in ynotranslations
   (their README welcomes pull requests), so the browser version benefits
   too. Check the translation guidelines first.
3. Use `build/translation-report.md` to pick remaining gaps; skip anything
   marked as must-stay-original.

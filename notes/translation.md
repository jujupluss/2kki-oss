# Translation pipeline (English)

Tool: `scripts/translate.py`. Commands:

```sh
make translate   # generate translation/en/*.po from xml/
make run-en      # play with --language-path translation --language en
```

`translation/en/` is gitignored (it contains the game's source text). Only the
curated `translation/glossary.json` and `translation/overrides.json` are
committed.

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

## Current coverage

| file | entries |
|---|---:|
| `rpg_rt.ldb.po` | 18735 |
| `rpg_rt.ldb.common.po` | 2617 |
| `rpg_rt.lmt.po` | 3931 |
| `Map*.po` (3930 files) | ~10384 |
| **total** | **35667** |

Translated so far: **112 entries (0.31%)**.

What is already English:

- all `Terms` UI strings (menu, save/load, yes/no, file prompts, level/HP
  labels, quit confirmation);
- the real-room PC menu in `Map0002` (24 curated full-message overrides):
  wallpaper menu, options, default restore, data/play-record pages, load and
  quit prompts, shift-key help, sound-test prompts, Time Trial notices;
- common one-word choices and labels (`はい/いいえ`, `セーブ`, `ロード`,
  `おきる`, `やめる`, `もどる`, `夢`, `現実`, …), including multi-line choice
  lists translated line by line;
- protagonist name `うろつき` -> `Wanderer`.

The whole game has only ~4k unique message lines (Map0001, the debug room,
accounts for a big chunk), so a complete pass is feasible but is content work,
not tooling.

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
`translation/en/*.po`; it is regenerated.

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

1. Translate `Map0002` to 100% (PC menu is the main UI).
2. Translate the first real dream maps (0004/0005, 0010 door room).
3. Work through maps by frequency; ~4k unique lines total.
4. Optionally add a `--freq` report to rank untranslated lines.

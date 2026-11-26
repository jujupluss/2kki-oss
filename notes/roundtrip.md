# LCF <-> XML round-trip notes

Tool: `build/liblcf/lcf2xml` (liblcf 0.8.1, built from `vendor/liblcf`).
Always pass `--2k --encoding CP932`.

## Byte-identical conversion

Converting a file to XML and back reproduces the original bytes exactly:

```
$ lcf2xml --2k --encoding CP932 Map0001.lmu      # -> Map0001.emu
$ lcf2xml --2k --encoding CP932 Map0001.emu      # -> Map0001.lmu
$ cmp Map0001.lmu <game>/Map0001.lmu             # identical
```

`make extract` keeps the original binary LCF files in `build/game/` (copied,
never hardlinked). When it converts a file to XML in the same run it touches
the LCF afterwards, so `make data` only rebuilds XML files that were edited
and untouched maps stay byte-exact. When the XML was already there (cached,
possibly edited), extract marks the LCF older than the XML instead, so the
next `make data` rebuilds it rather than silently keeping the original.

## Full rebuild after the CR fix

After escaping CR (see below), rebuilding **all 3932 XML files** and comparing
to `build/game` gives:

| result | files |
|---|---:|
| byte-identical | **3921 / 3930 maps** |
| differ | **9 maps** |
| `RPG_RT.ldb`, `RPG_RT.lmt` | identical |

The 9 remaining maps differ only in non-player-visible metadata:

1. **`save_count_2k3e`** (9 maps: `Map1042, 1604, 1969, 2813, 2818, 2820,
   2994, 3093, 3219`). This is an RPG Maker 2003 English-only counter. liblcf
   zeroes it when saving as RPG Maker 2000 (`--2k`). The game is 2k, so the
   field is unused.
2. **PUA-mapped C0 control characters in event names** (`Map1604`, `Map1969`).
   The source LCF has `0x1F` inside an event `<name>`; the XML writer maps C0
   controls to U+E000..U+E01F, and liblcf's reader only restores part of that
   round-trip. The affected field is the editor event name, not game text.

Neither is worth a custom patch; the practical workflow below never rebuilds
an unedited map.

## The CR problem and the fix

Some event message strings end with a literal `\r` (`0x0D`). A literal CR in
XML content is normalised to LF by every XML parser, so `XML -> LCF` lost it.
That made 31 maps non-round-trippable.

`scripts/extract.py` now post-processes every generated XML file and replaces
raw `0x0D` with the character reference `&#xD;`:

```
raw  ...する\r</string>
XML  ...する&#xD;</string>
```

The XML parser then restores CR, and the round-trip is byte-exact. This is on
by default; `--no-cr-escape` disables it.

There is also an upstream fix for liblcf (the writer should emit `&#xD;`
itself):

```sh
make patch-liblcf      # applies patches/liblcf-escape-cr.patch, rebuilds lcf2xml
```

The patch modifies the `vendor/liblcf` submodule working tree, so the
superproject shows it as dirty. It is optional: the extractor's
post-processing already makes the pipeline correct without it.

## Practical rule

- `make extract` — derive `build/game` + `xml/` from `original/`.
- edit `xml/*.emu`.
- `make data` — rebuild only the edited maps into `build/game`.
- `make run` — play.
- `make xml` — refresh XML from `build/game` without touching the game tree
  (`--xml-only`), useful after re-applying the map-tree prune.

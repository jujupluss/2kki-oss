# Map tree cleanup — findings

Tool: `scripts/map_tree.py` (`make map-tree`, `make prune-tree`).
Source: `xml/RPG_RT.emt` (LFMT). Report: `build/map-tree-report.md`,
machine data `build/map-tree.json`.

## Problem

`RPG_RT.emt` had **4601 `MapInfo` nodes** but only **3930 `Map*.lmu` files**.
The tree kept 670 entries for maps that were deleted over the years. 31 of
those dangling nodes were still parents of existing maps, and 7 existing maps
inherited their BGM from a dangling node.

## Result

| | before | after |
|---|---:|---:|
| `MapInfo` nodes | 4601 | **3931** (root + 3930 maps) |
| dangling map nodes | 670 | **0** |
| kept maps nested under a dangling parent | 31 | 0 |
| effective music/background differences | — | **0** |

- Removed 670 dangling map nodes (root id 0 kept).
- Reparented 31 kept maps to their nearest kept ancestor.
- Materialised inherited BGM for 7 maps (`MAP3345`, `MAP3348`…`MAP3353`),
  which inherited from the removed node 2427.
- Recomputed `indentation` from the new parent chain.
- Verified: every `parent_map` points at a kept node, no cycles, every `.lmu`
  is present in the tree, and the effective music/background of all 3930 maps
  is bit-for-bit identical to before the prune.

The pruned tree converts back to `RPG_RT.lmt` (3931 `MapInfo`) and EasyRPG
Player boots the game with it without errors.

## Commands

```sh
make map-tree     # analyse + write build/pruned/RPG_RT.emt (no changes)
make prune-tree   # apply to xml/RPG_RT.emt and rebuild build/game/RPG_RT.lmt
```

`--apply` writes a backup to `build/RPG_RT.emt.bak` first (only if none
exists yet, so it always holds the unpruned tree). Because `xml/` is
derived, a fresh clone needs `make prune-tree` after `make extract`.
`make extract` does **not** overwrite an existing `xml/RPG_RT.emt` unless you
pass `--force-xml`, so the pruned tree survives normal extraction runs.

## What was removed

Contiguous ranges of removed ids (examples): `1049-1050`, `1597-1600`,
`1682-1690`, `1832-1837`, `…`. The full list is in `build/map-tree.json` under
`removed_ids` / `removed_ranges`.

The removed nodes were `MAP####` placeholders with no map file; none had a
playable map. If a later patch restores one of these maps, re-run
`make extract` (which rebuilds the tree from `original/`) and re-apply the
patch logic.

## Caveat

`music_type == 0` / `background_type == 0` mean "inherit from parent" (verified
in `vendor/Player/src/game_map.cpp`). That is why materialising inherited BGM
was necessary; without it the 7 affected maps would have changed music.

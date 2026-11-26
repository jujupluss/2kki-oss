#!/usr/bin/env python3
"""Analyse and prune the RPG Maker 2000 map tree (RPG_RT.lmt / .emt).

The shipped 0.129h tree has 4601 ``MapInfo`` nodes but only 3930 ``.lmu``
files. 670 map nodes are dangling (the maps were deleted over the years).
Some *existing* maps are nested under a dangling node, and some inherit BGM
from one.

This tool:

  1. keeps the root, every node that has a ``Map*.lmu``, and every area
     node (which never has a file) whose parent map is kept;
  2. removes dangling map nodes;
  3. reparents kept nodes to their nearest kept ancestor;
  4. materialises inherited music/background when the resolved ancestor was a
     removed node (so the audible result does not change);
  5. recomputes indentation.

By default it writes ``build/RPG_RT.emt.pruned`` and reports; ``--apply``
overwrites the input XML (after a backup).  Use ``make data`` afterwards to
rebuild ``build/game/RPG_RT.lmt``.
"""

from __future__ import annotations

import argparse
import copy
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT_ID = 0
AREA_TYPE = 2   # lcf::rpg::TreeMap::MapType: 0 root, 1 map, 2 area


def set_text(el: ET.Element, tag: str, value: object) -> None:
    child = el.find(tag)
    if child is None:
        child = ET.SubElement(el, tag)
    child.text = str(value)


class Tree:
    def __init__(self, path: Path):
        self.path = path
        self.tree = ET.parse(path)
        self.root = self.tree.getroot()
        self.maps_el = self.root.find("TreeMap/maps")
        if self.maps_el is None:
            raise SystemExit(f"error: no TreeMap/maps in {path}")

        self.order: list[int] = []
        self.nodes: dict[int, dict] = {}
        for el in self.maps_el.findall("MapInfo"):
            mid = int(el.get("id"))
            self.order.append(mid)
            self.nodes[mid] = {
                "id": mid,
                "el": el,
                "name": el.findtext("name", ""),
                "parent": int(el.findtext("parent_map", "0") or 0),
                "indent": int(el.findtext("indentation", "0") or 0),
                "type": int(el.findtext("type", "-1") or -1),
                "music_type": int(el.findtext("music_type", "0") or 0),
                "music_el": el.find("music"),
                "background_type": int(el.findtext("background_type", "0") or 0),
                "background_name": el.findtext("background_name", ""),
            }

    def parent_of(self, mid: int) -> int | None:
        node = self.nodes.get(mid)
        if node is None:
            return None
        p = node["parent"]
        return p if p in self.nodes and p != mid else None

    def resolve(self, mid: int, key: str) -> int:
        """Follow the inheritance chain (Player semantics)."""
        cur = mid
        seen = {cur}
        while self.nodes[cur][key] == 0:
            p = self.parent_of(cur)
            if p is None or p in seen:
                break
            seen.add(p)
            cur = p
        return cur


def contiguous_ranges(ids: list[int]) -> list[str]:
    if not ids:
        return []
    out: list[str] = []
    start = prev = ids[0]
    for x in ids[1:]:
        if x == prev + 1:
            prev = x
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = x
    out.append(f"{start}" if start == prev else f"{start}-{prev}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lmt", default="xml/RPG_RT.emt")
    ap.add_argument("--game", default="build/game")
    ap.add_argument("--out", default="build/pruned/RPG_RT.emt")
    ap.add_argument("--json", default="build/map-tree.json")
    ap.add_argument("--report", default="build/map-tree-report.md")
    ap.add_argument("--backup", default="build/RPG_RT.emt.bak")
    ap.add_argument("--apply", action="store_true",
                    help="overwrite --lmt with the pruned tree")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lmt = Path(args.lmt)
    game = Path(args.game)
    tree = Tree(lmt)

    existing = {int(p.stem[3:]) for p in game.glob("Map*.lmu")}
    if not existing:
        raise SystemExit(f"error: no Map*.lmu in {game}")
    kept = {ROOT_ID} | (existing & set(tree.nodes))
    kept |= {mid for mid, n in tree.nodes.items()
             if n["type"] == AREA_TYPE and n["parent"] in kept}
    missing_in_tree = sorted(existing - set(tree.nodes))

    dangling = sorted(i for i in tree.order if i not in kept)

    # ---- reparent kept nodes --------------------------------------------
    def nearest_kept(mid: int) -> int:
        p = tree.parent_of(mid)
        seen = set()
        while p is not None and p not in kept:
            if p in seen:
                break
            seen.add(p)
            p = tree.parent_of(p)
        return p if p is not None else ROOT_ID

    reparented: list[tuple[int, int, int]] = []
    new_parent: dict[int, int] = {}
    for mid in tree.order:
        if mid not in kept:
            continue
        if mid == ROOT_ID:
            new_parent[mid] = ROOT_ID
            continue
        np = nearest_kept(mid)
        new_parent[mid] = np
        if np != tree.nodes[mid]["parent"]:
            reparented.append((mid, tree.nodes[mid]["parent"], np))

    # ---- materialise inherited music / background -----------------------
    materialised_music: list[tuple[int, int]] = []
    materialised_bg: list[tuple[int, int]] = []
    for mid in tree.order:
        if mid not in kept:
            continue
        node = tree.nodes[mid]
        if node["music_type"] == 0:
            eff = tree.resolve(mid, "music_type")
            if eff not in kept:
                src = tree.nodes[eff]
                node["music_type"] = src["music_type"]
                if src["music_el"] is not None and node["music_el"] is not None:
                    parent_el = node["el"]
                    idx = list(parent_el).index(node["music_el"])
                    parent_el.remove(node["music_el"])
                    parent_el.insert(idx, copy.deepcopy(src["music_el"]))
                    node["music_el"] = parent_el[idx]
                materialised_music.append((mid, eff))
        if node["background_type"] == 0:
            eff = tree.resolve(mid, "background_type")
            if eff not in kept:
                src = tree.nodes[eff]
                node["background_type"] = src["background_type"]
                node["background_name"] = src["background_name"]
                materialised_bg.append((mid, eff))

    # ---- write kept values ----------------------------------------------
    for mid in tree.order:
        if mid not in kept:
            continue
        node = tree.nodes[mid]
        set_text(node["el"], "parent_map", new_parent[mid])
        set_text(node["el"], "music_type", node["music_type"])
        set_text(node["el"], "background_type", node["background_type"])
        set_text(node["el"], "background_name", node["background_name"])

    # ---- indentation from the new parent chain --------------------------
    depth: dict[int, int] = {}

    def get_depth(mid: int) -> int:
        if mid in depth:
            return depth[mid]
        p = new_parent.get(mid, ROOT_ID)
        d = 0 if p == mid else get_depth(p) + 1
        depth[mid] = d
        return d

    for mid in tree.order:
        if mid in kept:
            set_text(tree.nodes[mid]["el"], "indentation", get_depth(mid))

    # ---- remove dangling -------------------------------------------------
    for mid in dangling:
        tree.maps_el.remove(tree.nodes[mid]["el"])

    kept_nodes = [tree.nodes[i] for i in tree.order if i in kept]
    result = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": str(lmt),
        "entries_before": len(tree.order),
        "entries_after": len(kept_nodes),
        "removed": len(dangling),
        "removed_ids": dangling,
        "removed_ranges": contiguous_ranges(dangling),
        "reparented": [
            {"id": i, "old_parent": o, "new_parent": n} for i, o, n in reparented
        ],
        "materialised_music": [
            {"id": i, "from": e} for i, e in materialised_music
        ],
        "materialised_background": [
            {"id": i, "from": e} for i, e in materialised_bg
        ],
        "map_files_missing_from_tree": missing_in_tree,
        "maps": [
            {
                "id": n["id"], "name": n["name"],
                "parent": new_parent[n["id"]],
                "depth": get_depth(n["id"]),
                "music_type": n["music_type"],
                "background_type": n["background_type"],
            }
            for n in kept_nodes
        ],
    }

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1))

    lines = [
        "# Map tree report",
        "",
        f"Generated: {result['generated']} from `{lmt}`",
        "",
        f"- entries before: **{result['entries_before']}**",
        f"- entries after: **{result['entries_after']}**",
        f"- removed dangling map nodes: **{result['removed']}**",
        f"- reparented kept maps: **{len(reparented)}**",
        f"- materialised inherited music: **{len(materialised_music)}**",
        f"- materialised inherited background: **{len(materialised_bg)}**",
        "",
        "## Removed id ranges",
        "",
        ", ".join(f"`{r}`" for r in result["removed_ranges"]) or "_None._",
        "",
        "## Reparented maps",
        "",
    ]
    for i, o, n in reparented:
        lines.append(f"- `MAP{i:04d}` (`{tree.nodes[i]['name']}`): {o} -> {n}")
    lines += ["", "## Materialised music", ""]
    for i, e in materialised_music:
        lines.append(f"- `MAP{i:04d}` took music from removed node {e}")
    if not materialised_music:
        lines.append("_None._")
    lines += ["", "## Map files missing from tree", ""]
    lines.append(", ".join(f"`{i}`" for i in missing_in_tree) or "_None._")
    lines.append("")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(lines) + "\n")

    # ---- output ----------------------------------------------------------
    if not args.dry_run:
        if args.apply:
            backup = Path(args.backup)
            # Keep the first backup: a second --apply would otherwise
            # replace the unpruned tree with an already-pruned one.
            if backup.exists():
                print(f"backup   {backup} exists, not overwritten")
            else:
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(lmt.read_bytes())
            tree.tree.write(lmt, encoding="utf-8", xml_declaration=True)
            print(f"applied  {lmt} (backup: {backup})")
        else:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            tree.tree.write(args.out, encoding="utf-8", xml_declaration=True)
            print(f"wrote    {args.out}")

    print(f"entries  {result['entries_before']} -> {result['entries_after']} "
          f"(-{result['removed']})")
    print(f"reparented {len(reparented)}, music materialised {len(materialised_music)}, "
          f"background {len(materialised_bg)}")
    print(f"report   {args.report}")
    print(f"json     {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

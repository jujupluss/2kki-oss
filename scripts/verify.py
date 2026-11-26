#!/usr/bin/env python3
"""Sanity-check an extracted Yume 2kki game tree.

Checks:
  * required files exist in the game dir
  * RPG_RT.ini declares [EasyRPG] Encoding=932
  * no CP866 mojibake path components survive
  * every LCF file has a matching XML file in xml/
  * a few asset references from the LDB resolve on disk

Exit code 0 = all good, 1 = problems found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LCF_EXTS = {".lmu", ".ldb", ".lmt", ".lsd"}
XML_EXT = {".lmu": ".emu", ".ldb": ".edb", ".lmt": ".emt", ".lsd": ".esd"}

# Characters that only appear in CP866-decoded CP932 mojibake.
MOJIBAKE_RANGES = [
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x04FF),  # Cyrillic
    (0x2500, 0x257F),  # box drawing
    (0x2580, 0x259F),  # block elements
]


def is_mojibake(name: str) -> bool:
    for ch in name:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in MOJIBAKE_RANGES):
            return True
    return False


def find_asset(asset_dir: Path, name: str) -> Path | None:
    for ext in (".png", ".bmp", ".xyz"):
        p = asset_dir / (name + ext)
        if p.is_file():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game", default="build/game")
    ap.add_argument("--xml", default="xml")
    args = ap.parse_args()

    game = Path(args.game)
    xml = Path(args.xml)
    problems: list[str] = []
    notes: list[str] = []

    # ---- required files --------------------------------------------------
    for req in ("RPG_RT.ldb", "RPG_RT.lmt", "RPG_RT.ini"):
        if not (game / req).is_file():
            problems.append(f"missing {req} in {game}")

    # ---- ini encoding ----------------------------------------------------
    ini = game / "RPG_RT.ini"
    if ini.is_file():
        text = ini.read_bytes().decode("cp932", errors="replace")
        if "Encoding=932" not in text:
            problems.append("RPG_RT.ini does not contain [EasyRPG] Encoding=932")
        else:
            notes.append("RPG_RT.ini declares Encoding=932")
        m = re.search(r"GameTitle=(.*)", text)
        if m:
            notes.append(f"GameTitle = {m.group(1).strip()}")

    # ---- mojibake scan ---------------------------------------------------
    bad_names: list[str] = []
    for p in game.rglob("*"):
        if is_mojibake(p.name):
            bad_names.append(str(p.relative_to(game)))
            if len(bad_names) >= 20:
                break
    if bad_names:
        problems.append(f"{len(bad_names)}+ mojibake path component(s) remain")
        for n in bad_names[:10]:
            problems.append(f"    {n}")

    # ---- LCF <-> XML parity ---------------------------------------------
    lcf_files = [p for p in game.rglob("*") if p.is_file() and p.suffix.lower() in LCF_EXTS]
    missing_xml = []
    for p in lcf_files:
        out = xml / (p.stem + XML_EXT[p.suffix.lower()])
        if not out.is_file():
            missing_xml.append(str(p.relative_to(game)))
    if missing_xml:
        problems.append(f"{len(missing_xml)} LCF file(s) without XML (e.g. {missing_xml[0]})")
    else:
        notes.append(f"{len(lcf_files)} LCF files all have XML")

    # ---- asset references from LDB XML ----------------------------------
    edb = xml / "RPG_RT.edb"
    if edb.is_file():
        head = edb.read_text(encoding="utf-8", errors="replace")[:200000]
        for tag, folder in (
            ("title_name", "Title"),
            ("system_name", "System"),
            ("gameover_name", "Gameover"),
        ):
            for m in re.finditer(rf"<{tag}>([^<]+)</{tag}>", head):
                name = m.group(1)
                if not name or name.lower() in {"(off)", "(none)"}:
                    continue
                found = find_asset(game / folder, name)
                if found is None:
                    problems.append(f"{tag} '{name}' not found in {folder}/")
                else:
                    notes.append(f"{tag} '{name}' -> {found.relative_to(game)}")

    # ---- asset dir counts ------------------------------------------------
    for d in sorted(p for p in game.iterdir() if p.is_dir()):
        n = sum(1 for _ in d.rglob("*") if _.is_file())
        notes.append(f"{d.name}/: {n} files")

    # ---- report ----------------------------------------------------------
    print(f"game dir: {game}")
    print(f"xml dir : {xml}")
    print()
    for n in notes:
        print(f"  ok   {n}")
    for p in problems:
        print(f"  FAIL {p}")

    print()
    if problems:
        print(f"{len(problems)} problem(s)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

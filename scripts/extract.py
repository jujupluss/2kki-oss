#!/usr/bin/env python3
"""Extract Yume 2kki game data into a runnable game dir and XML.

Why this exists
---------------
The distributed archive was unpacked with CP932 (Shift-JIS) bytes decoded as
CP866 (DOS Cyrillic), so every Japanese filename in ``original/`` is mojibake.
The game data (RPG_RT.ldb / *.lmu) still stores CP932 names, so the on-disk
names *must* be repaired or EasyRPG Player cannot find the assets.

This script:

  1. locates the base game root and any patch roots under ``original/``;
  2. repairs every path component (CP866 -> CP932 -> UTF-8);
  3. builds ``build/game/`` (hardlinks by default, or copies) with repaired
     names, applying patch overlays on top of the base game;
  4. writes a patched ``RPG_RT.ini`` with ``[EasyRPG] Encoding=932`` so the
     Player does not fall back to the 1252 locale;
  5. converts all LCF files to XML in ``xml/`` via ``lcf2xml``;
  6. writes ``build/manifest.json`` and ``notes/extract-report.md``.

``original/`` is only ever read. See ``notes/encoding.md`` for the evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MOJIBAKE_ENC = "cp866"   # what the archive was wrongly decoded as
TARGET_ENC = "cp932"     # what the game data actually uses
LCF_EXTS = {".lmu", ".ldb", ".lmt", ".lsd"}
XML_EXT = {".lmu": ".emu", ".ldb": ".edb", ".lmt": ".emt", ".lsd": ".esd"}
INI_NAME = "RPG_RT.ini"


# --------------------------------------------------------------------------
# mojibake repair
# --------------------------------------------------------------------------
def repair_component(name: str) -> tuple[str, bool]:
    """Return (repaired, changed) for one path component."""
    if not any(ord(c) > 127 for c in name):
        return name, False
    try:
        raw = name.encode(MOJIBAKE_ENC)
    except UnicodeEncodeError:
        return name, False
    try:
        fixed = raw.decode(TARGET_ENC)
    except UnicodeDecodeError:
        return name, False
    if "\ufffd" in fixed or not fixed:
        return name, False
    return fixed, True


def repair_relpath(rel: Path) -> tuple[Path, bool]:
    changed = False
    parts: list[str] = []
    for part in rel.parts:
        fixed, ch = repair_component(part)
        changed = changed or ch
        parts.append(fixed)
    return Path(*parts), changed


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def find_lcf_roots(src: Path) -> list[Path]:
    roots: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(src):
        if INI_NAME in filenames or "RPG_RT.ldb" in filenames:
            roots.append(Path(dirpath))
    return roots


def choose_roots(src: Path) -> tuple[Path, list[Path]]:
    roots = find_lcf_roots(src)
    if not roots:
        sys.exit(f"error: no RPG_RT.ldb found under {src}")

    def lmu_count(p: Path) -> int:
        return sum(1 for _ in p.glob("*.lmu"))

    # Judge "patch" by the path *inside* src only: the checkout itself may
    # live under a directory such as ~/2kki-patches/.
    def is_patch(p: Path) -> bool:
        return "patch" in str(p.relative_to(src)).lower()

    def natural_key(p: Path) -> list:
        # patch2 before patch10, so overlays apply in release order.
        return [int(t) if t.isdigit() else t.lower()
                for t in re.split(r"(\d+)", str(p.relative_to(src)))]

    base_candidates = [p for p in roots if not is_patch(p)]
    patch_roots = sorted((p for p in roots if is_patch(p)), key=natural_key)

    if not base_candidates:
        # everything lives under a patch-named dir; fall back to the biggest
        base_candidates = roots
        patch_roots = []
    base = max(base_candidates, key=lmu_count)
    patch_roots = [p for p in patch_roots if p != base]
    return base, patch_roots


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def place(src: Path, dst: Path, use_copy: bool, dry: bool) -> None:
    if dry:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    # LCF files are the ones we rebuild from XML, so never hardlink them:
    # truncating a hardlink in build/game would corrupt original/.
    if use_copy or dst.suffix.lower() in LCF_EXTS:
        shutil.copy2(src, dst)
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def iter_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def escape_cr_in_xml(path: Path) -> bool:
    """Replace raw CR bytes with the XML character reference ``&#xD;``.

    A literal CR in XML content is normalised to LF by every parser, which
    loses it on the way back to LCF. ``&#xD;`` survives, so the round-trip is
    byte-exact. See notes/roundtrip.md.
    """
    data = path.read_bytes()
    if b"\r" not in data:
        return False
    path.write_bytes(data.replace(b"\r", b"&#xD;"))
    return True


def write_ini(src_ini: Path, dst_ini: Path, dry: bool) -> None:
    raw = src_ini.read_bytes()
    text = raw.decode(TARGET_ENC, errors="replace")
    if "[EasyRPG]" not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n[EasyRPG]\nEncoding=932\n"
    if not dry:
        dst_ini.parent.mkdir(parents=True, exist_ok=True)
        # dst may be a hardlink to original/ (place() links non-LCF files);
        # writing through it would modify the read-only source.
        if dst_ini.exists() or dst_ini.is_symlink():
            dst_ini.unlink()
        dst_ini.write_bytes(text.encode(TARGET_ENC))


def convert_lcf_to_xml(game_dir: Path, xml_dir: Path, lcf2xml: Path,
                       force_xml: bool, maps_limit: int, no_cr_escape: bool,
                       dry_run: bool
                       ) -> tuple[list[Path], int, int, int, set[Path]]:
    """Convert every LCF file in game_dir to XML in xml_dir.

    Returns (xml files, xml bytes, cached count, CR-escaped count, the set
    of LCF paths whose XML was (re)generated in this run).
    """
    xml_files: list[Path] = []
    xml_bytes = 0
    xml_skipped = 0
    cr_escaped = 0
    converted: set[Path] = set()
    if not lcf2xml.is_file():
        print(f"warning: lcf2xml not found at {lcf2xml}; skipping XML", file=sys.stderr)
        return xml_files, xml_bytes, xml_skipped, cr_escaped, converted

    xml_dir.mkdir(parents=True, exist_ok=True)
    lcf_files = sorted(
        p for p in game_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in LCF_EXTS
    )
    maps = [p for p in lcf_files if p.suffix.lower() == ".lmu"]
    if maps_limit and len(maps) > maps_limit:
        keep = set(maps[:maps_limit])
        lcf_files = [p for p in lcf_files if p.suffix.lower() != ".lmu" or p in keep]

    todo: list[Path] = []
    for p in lcf_files:
        out = xml_dir / (p.stem + XML_EXT[p.suffix.lower()])
        # XML is derived from the read-only original/; cache on existence,
        # use --force-xml to refresh.
        if not force_xml and out.is_file() and out.stat().st_size > 0:
            xml_skipped += 1
            continue
        todo.append(p)

    print(f"LCF files      : {len(lcf_files)} ({len(todo)} to convert, {xml_skipped} cached)")
    if not no_cr_escape:
        print("CR escaping    : on (raw CR -> &#xD;)")
    chunk = 200
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        cmd = [str(lcf2xml), "--2k", "--encoding", TARGET_ENC] + [str(p) for p in batch]
        if dry_run:
            print(f"  [dry-run] {' '.join(cmd[:4])} ... ({len(batch)} files)")
            continue
        res = subprocess.run(cmd, cwd=str(xml_dir))
        if res.returncode != 0:
            raise RuntimeError(f"lcf2xml failed on batch {i // chunk + 1}")
        if not no_cr_escape:
            for p in batch:
                out = xml_dir / (p.stem + XML_EXT[p.suffix.lower()])
                if out.is_file() and escape_cr_in_xml(out):
                    cr_escaped += 1
        converted.update(batch)
        print(f"  converted {min(i + chunk, len(todo))}/{len(todo)}")

    xml_files = sorted(xml_dir.glob("*"))
    xml_bytes = sum(p.stat().st_size for p in xml_files if p.is_file())
    return xml_files, xml_bytes, xml_skipped, cr_escaped, converted


def sync_lcf_mtimes(lcf_files: list[Path], xml_dir: Path,
                    converted: set[Path]) -> int:
    """Set LCF mtimes so ``make data`` (rebuild.py) does the right thing.

    rebuild.py rebuilds an LCF file when its XML is newer. After this run:

    * XML converted in this run matches its LCF byte for byte: mark the
      LCF fresh so untouched maps keep their exact original bytes.
    * XML that was *cached* may hold edits (or the pruned map tree) that
      the freshly copied original LCF does not: mark the LCF older than
      its XML so the next ``make data`` rebuilds it.

    Returns the number of LCF files left stale.
    """
    stale = 0
    for lcf in lcf_files:
        if lcf.suffix.lower() not in LCF_EXTS or not lcf.is_file():
            continue
        xml = xml_dir / (lcf.stem + XML_EXT[lcf.suffix.lower()])
        if lcf in converted or not xml.is_file():
            os.utime(lcf, None)
        else:
            t = xml.stat().st_mtime_ns - 1_000_000_000
            os.utime(lcf, ns=(t, t))
            stale += 1
    return stale


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--src", default="original", help="source tree (default: original)")
    ap.add_argument("--game", default="build/game", help="runnable game dir (default: build/game)")
    ap.add_argument("--xml", default="xml", help="XML output dir (default: xml)")
    ap.add_argument("--lcf2xml", default="build/liblcf/lcf2xml")
    ap.add_argument("--copy", action="store_true", help="copy files instead of hardlinking")
    ap.add_argument("--no-xml", action="store_true", help="skip LCF -> XML conversion")
    ap.add_argument("--xml-only", action="store_true",
                    help="only refresh XML from an existing game dir")
    ap.add_argument("--force-xml", action="store_true", help="reconvert XML even if up to date")
    ap.add_argument("--maps-limit", type=int, default=0, help="only convert N maps (0 = all)")
    ap.add_argument("--no-cr-escape", action="store_true",
                    help="keep literal CR in XML (not round-trip safe)")
    ap.add_argument("--hash", choices=["none", "lcf", "all"], default="lcf")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest", default="build/manifest.json")
    ap.add_argument("--report", default="notes/extract-report.md")
    args = ap.parse_args()

    t0 = time.time()
    src = Path(args.src).resolve()
    game_dir = Path(args.game).resolve()
    xml_dir = Path(args.xml).resolve()
    lcf2xml = Path(args.lcf2xml).resolve()

    if args.xml_only:
        try:
            files, nbytes, skipped, escaped, converted = convert_lcf_to_xml(
                game_dir, xml_dir, lcf2xml, args.force_xml, args.maps_limit,
                args.no_cr_escape, args.dry_run)
        except RuntimeError as exc:
            sys.exit(f"error: {exc}")
        # Freshly converted XML matches the LCF it came from.
        if not args.dry_run:
            for lcf in converted:
                os.utime(lcf, None)
        print()
        print(f"XML files {len(files)} ({nbytes / 1048576:.1f} MiB), "
              f"{skipped} cached, {escaped} CR-escaped")
        return 0

    if not src.is_dir():
        sys.exit(f"error: source dir not found: {src}")
    base, patch_roots = choose_roots(src)
    print(f"base game root : {base}")
    for p in patch_roots:
        print(f"patch root     : {p}")

    # ---- plan base game files -------------------------------------------
    plan: list[tuple[Path, Path, bool, str]] = []  # (src, dst, repaired, layer)
    seen: dict[Path, Path] = {}
    collisions: list[tuple[Path, Path, Path]] = []
    repaired_count = 0
    repaired_samples: list[tuple[str, str]] = []

    for f in iter_files(base):
        rel = f.relative_to(base)
        fixed_rel, changed = repair_relpath(rel)
        dst = game_dir / fixed_rel
        if dst in seen:
            collisions.append((dst, seen[dst], f))
            continue
        seen[dst] = f
        plan.append((f, dst, changed, "base"))
        if changed:
            repaired_count += 1
            if len(repaired_samples) < 40:
                repaired_samples.append((str(rel), str(fixed_rel)))

    # ---- outer-level docs (read_me, changelog, license) -----------------
    outer = base.parent
    for f in sorted(outer.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() in {".exe", ".ico"}:
            continue
        fixed, changed = repair_component(f.name)
        dst = game_dir / fixed
        if dst in seen:
            continue
        seen[dst] = f
        plan.append((f, dst, changed, "outer"))
        if changed:
            repaired_count += 1
            if len(repaired_samples) < 40:
                repaired_samples.append((f.name, fixed))

    # ---- patch overlay ---------------------------------------------------
    patch_plan: list[tuple[Path, Path, bool, str]] = []
    for proot in patch_roots:
        for f in iter_files(proot):
            rel = f.relative_to(proot)
            fixed_rel, changed = repair_relpath(rel)
            dst = game_dir / fixed_rel
            patch_plan.append((f, dst, changed, "patch"))
            if changed:
                repaired_count += 1
                if len(repaired_samples) < 40:
                    repaired_samples.append((str(rel), str(fixed_rel)))

    if collisions:
        print("error: repaired-name collisions:", file=sys.stderr)
        for dst, a, b in collisions[:20]:
            print(f"  {dst}\n    <- {a}\n    <- {b}", file=sys.stderr)
        return 2

    print(f"planned files  : {len(plan)} base/outer + {len(patch_plan)} patch")
    print(f"repaired names : {repaired_count}")

    # ---- materialise game dir -------------------------------------------
    if not args.dry_run:
        game_dir.mkdir(parents=True, exist_ok=True)
    for s, d, _ch, _layer in plan:
        place(s, d, args.copy, args.dry_run)
    for s, d, _ch, _layer in patch_plan:
        place(s, d, args.copy, args.dry_run)

    # Patch layers override the base, so the last layer to ship an ini wins.
    ini_dst = game_dir / INI_NAME
    ini_src = None
    for s, d, _ch, _layer in plan + patch_plan:
        if d == ini_dst:
            ini_src = s
    if ini_src is not None:
        write_ini(ini_src, ini_dst, args.dry_run)

    # ---- LCF -> XML ------------------------------------------------------
    xml_files: list[Path] = []
    xml_bytes = 0
    xml_skipped = 0
    cr_escaped = 0
    converted: set[Path] = set()
    if not args.no_xml:
        try:
            xml_files, xml_bytes, xml_skipped, cr_escaped, converted = convert_lcf_to_xml(
                game_dir, xml_dir, lcf2xml, args.force_xml, args.maps_limit,
                args.no_cr_escape, args.dry_run)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    # ---- LCF timestamps --------------------------------------------------
    if not args.dry_run:
        stale = sync_lcf_mtimes(sorted({d for _s, d, _c, _l in plan + patch_plan}),
                                xml_dir, converted)
        if stale:
            print(f"cached XML     : {stale} LCF file(s) marked for rebuild "
                  "(run `make data`)")

    # ---- manifest --------------------------------------------------------
    def hash_wanted(suffix: str) -> bool:
        if args.hash == "all":
            return True
        if args.hash == "lcf":
            return suffix.lower() in LCF_EXTS or suffix.lower() in {".ini", ".txt"}
        return False

    entries = []
    for s, d, ch, layer in plan + patch_plan:
        try:
            size = s.stat().st_size
        except OSError:
            continue
        rec = {
            "src": str(s.relative_to(src)),
            "dst": str(d.relative_to(game_dir)),
            "layer": layer,
            "repaired": ch,
            "size": size,
        }
        if hash_wanted(s.suffix):
            rec["sha256"] = sha256(s)
        entries.append(rec)

    manifest = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "encoding": {"mojibake": MOJIBAKE_ENC, "target": TARGET_ENC},
        "base_root": str(base.relative_to(src)),
        "patch_roots": [str(p.relative_to(src)) for p in patch_roots],
        "counts": {
            "files": len(entries),
            "repaired_names": repaired_count,
            "xml_files": len(xml_files),
            "xml_bytes": xml_bytes,
            "cr_escaped": cr_escaped,
        },
        "files": entries,
    }
    if not args.dry_run:
        Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
        Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    # ---- report ----------------------------------------------------------
    elapsed = time.time() - t0
    lines = [
        "# Extract report",
        "",
        f"Generated: {manifest['generated']}  ({elapsed:.1f}s)",
        "",
        "## Roots",
        "",
        f"- base game: `{base.relative_to(src)}`",
    ]
    for p in patch_roots:
        lines.append(f"- patch: `{p.relative_to(src)}`")
    lines += [
        "",
        "## Encoding",
        "",
        f"- mojibake codec: `{MOJIBAKE_ENC}` (what the archive was wrongly decoded as)",
        f"- game codec: `{TARGET_ENC}` (RPG Maker 2000 / Shift-JIS)",
        f"- repaired path components: **{repaired_count}**",
        "",
        "## Outputs",
        "",
        f"- game dir: `{game_dir.relative_to(Path.cwd()) if game_dir.is_relative_to(Path.cwd()) else game_dir}`",
        f"- xml dir: `{xml_dir.relative_to(Path.cwd()) if xml_dir.is_relative_to(Path.cwd()) else xml_dir}`",
        f"- files placed: {len(entries)}",
        f"- XML files: {len(xml_files)} ({xml_bytes / 1048576:.1f} MiB)",
        f"- raw CR escaped as &#xD;: {cr_escaped}",
        "",
        "## Repaired name samples",
        "",
        "| original (mojibake) | repaired |",
        "|---|---|",
    ]
    for a, b in repaired_samples:
        lines.append(f"| `{a}` | `{b}` |")
    if not repaired_samples:
        lines.append("| _(none)_ | |")

    if not args.dry_run:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text("\n".join(lines) + "\n")

    print()
    print(f"done in {elapsed:.1f}s")
    print(f"  game dir : {game_dir}")
    print(f"  xml dir  : {xml_dir}")
    print(f"  manifest : {args.manifest}")
    print(f"  report   : {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Track original/edited XML as diffs against the game, not as a full copy.

The base game data is copyrighted and stays out of git. This tool records a
hash baseline of the extracted XML and exports only:

  * unified diffs for edited files (``content/patches/``),
  * full XML for brand-new files (``content/files/``),
  * a manifest (``content/content.json``).

Baseline XML is regenerated on demand from ``original/`` via ``lcf2xml`` and
the ``build/manifest.json`` mapping, so no multi-GB baseline copy is needed.

Commands
--------
  baseline  hash the current xml/ (run once, before editing)
  status    list edited / added / deleted XML since the baseline
  export    write patches + new files + content.json
  apply     apply content.json to xml/
  check     verify every exported patch still reproduces the current xml/

``RPG_RT.emt`` is tool-derived (map-tree prune) and is skipped; map-name
content belongs in the translation catalogue, not the tree XML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

# LCF -> XML extension (liblcf convention).
LCF_TO_XML = {".lmu": ".emu", ".ldb": ".edb", ".lmt": ".emt", ".lsd": ".esd"}
XML_TO_LCF = {v: k for k, v in LCF_TO_XML.items()}
SKIP = {"RPG_RT.emt"}  # map-tree prune is tooling, not content


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def hash_tree(root: Path) -> dict[str, str]:
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root)).replace(os.sep, "/")
            out[rel] = sha256_file(p)
    return out


def load_json(path: Path, default=None):
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def escape_cr(path: Path) -> None:
    data = path.read_bytes()
    if b"\r" in data:
        path.write_bytes(data.replace(b"\r", b"&#xD;"))


class Baseline:
    """Regenerate the untouched XML for one file from original/."""

    def __init__(self, manifest: Path, original: Path, lcf2xml: Path):
        data = load_json(manifest, {"files": []})
        self.dst_to_src = {e["dst"]: e["src"] for e in data.get("files", [])}
        self.original = original
        self.lcf2xml = lcf2xml
        self._tmp = Path(tempfile.mkdtemp(prefix="y2kki-baseline-"))

    def path_for(self, xml_rel: str) -> Path | None:
        suffix = Path(xml_rel).suffix
        lcf_suffix = XML_TO_LCF.get(suffix)
        if not lcf_suffix:
            return None
        lcf_rel = str(Path(xml_rel).with_suffix(lcf_suffix)).replace(os.sep, "/")
        src_rel = self.dst_to_src.get(lcf_rel)
        if not src_rel:
            return None
        src = self.original / src_rel
        if not src.is_file():
            return None
        out_dir = self._tmp / hashlib.sha1(xml_rel.encode()).hexdigest()
        out_dir.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            [str(self.lcf2xml), "--2k", "--encoding", "CP932", str(src.resolve())],
            cwd=out_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            return None
        out = out_dir / (Path(xml_rel).stem + suffix)
        if not out.is_file():
            return None
        escape_cr(out)
        return out

    def cleanup(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


def split_lines(text: str) -> list[str]:
    """Split on "\n" only, keeping it, exactly as `patch` sees lines.

    str.splitlines() also splits on \x0b, \x0c, \x1c-\x1e, \x85, \u2028
    and \u2029, which would produce hunks that `patch` cannot apply.
    """
    lines = text.split("\n")
    out = [line + "\n" for line in lines[:-1]]
    if lines[-1]:
        out.append(lines[-1])
    return out


def diff_files(old: Path, new: Path, rel: str) -> str:
    import difflib
    # newline="": no universal-newline translation, so a stray CR survives.
    # (open() rather than Path.read_text(newline=...), which needs 3.13.)
    with old.open(encoding="utf-8", newline="") as fh:
        old_lines = split_lines(fh.read())
    with new.open(encoding="utf-8", newline="") as fh:
        new_lines = split_lines(fh.read())
    out = []
    for line in difflib.unified_diff(old_lines, new_lines,
                                     fromfile=f"a/{rel}", tofile=f"b/{rel}"):
        # difflib leaves a last line without "\n" as is; patch needs the
        # marker git and diff(1) emit.
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        out.append(line)
    return "".join(out)


def apply_patch(xml: Path, patch: Path, dry_run: bool) -> tuple[str, str]:
    """Apply one exported patch to xml/ without ever prompting.

    Returns ("applied" | "already" | "conflict", patch output). Every check
    is a dry run first, so a patch that does not apply cleanly leaves xml/
    untouched (no half-applied hunks, no .rej files). A patch that is
    already applied (content-apply run twice) is reported, not reverted:
    plain `patch` would ask "Assume -R?" on the terminal.
    """
    base = ["patch", "-p1", "--no-backup-if-mismatch",
            "-d", str(xml), "-i", str(patch.resolve())]

    def run(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(base + list(extra), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)

    fwd = run("--forward", "--dry-run")
    if fwd.returncode == 0:
        if not dry_run:
            res = run("--forward")
            if res.returncode != 0:
                return "conflict", res.stdout + res.stderr
        return "applied", fwd.stdout
    if run("--reverse", "--force", "--dry-run").returncode == 0:
        return "already", ""
    return "conflict", fwd.stdout + fwd.stderr


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_baseline(args) -> int:
    xml = Path(args.xml)
    if not xml.is_dir():
        sys.exit(f"error: XML dir not found: {xml}")
    cur = hash_tree(xml)
    target = Path(args.content) / "baseline.json"
    if target.is_file() and not args.force:
        old = load_json(target, {}).get("files", {})
        if old != cur:
            changed = sum(1 for k in set(old) | set(cur) if old.get(k) != cur.get(k))
            sys.exit(f"error: current xml/ differs from the existing baseline "
                     f"({changed} files). Re-extract/prune first, or use --force.")
    save_json(target, {
        "version": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "files": cur,
    })
    print(f"baseline: {len(cur)} files -> {target}")
    return 0


def cmd_status(args) -> int:
    xml = Path(args.xml)
    base = load_json(Path(args.content) / "baseline.json")
    if not base:
        sys.exit("error: no baseline; run `content.py baseline` first")
    old = base["files"]
    cur = hash_tree(xml)
    modified = sorted(k for k in old.keys() & cur.keys()
                      if old[k] != cur[k] and k not in SKIP)
    added = sorted(k for k in cur.keys() - old.keys() if k not in SKIP)
    deleted = sorted(k for k in old.keys() - cur.keys() if k not in SKIP)
    print(f"modified {len(modified)}, added {len(added)}, deleted {len(deleted)}")
    for k in modified[:40]:
        print(f"  M {k}")
    for k in added[:40]:
        print(f"  A {k}")
    for k in deleted[:40]:
        print(f"  D {k}")
    return 0


def cmd_export(args) -> int:
    xml = Path(args.xml)
    content = Path(args.content)
    base = load_json(content / "baseline.json")
    if not base:
        sys.exit("error: no baseline; run `content.py baseline` first")
    old = base["files"]
    cur = hash_tree(xml)
    modified = sorted(k for k in old.keys() & cur.keys()
                      if old[k] != cur[k] and k not in SKIP)
    added = sorted(k for k in cur.keys() - old.keys() if k not in SKIP)
    deleted = sorted(k for k in old.keys() - cur.keys() if k not in SKIP)

    baseline = Baseline(Path(args.manifest), Path(args.original), Path(args.lcf2xml).resolve())
    patches_dir = content / "patches"
    files_dir = content / "files"
    for d in (patches_dir, files_dir):
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    entries_mod = []
    try:
        for rel in modified:
            old_path = baseline.path_for(rel)
            if old_path is None:
                print(f"warning: no baseline for {rel}; storing as full file",
                      file=sys.stderr)
                dst = files_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(xml / rel, dst)
                entries_mod.append({"path": rel, "file": f"files/{rel}", "kind": "full"})
                continue
            text = diff_files(old_path, xml / rel, rel)
            if not text:
                continue
            patch_rel = f"patches/{rel}.patch"
            p = content / patch_rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            entries_mod.append({"path": rel, "patch": patch_rel, "kind": "patch"})
    finally:
        baseline.cleanup()

    entries_add = []
    for rel in added:
        dst = files_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(xml / rel, dst)
        entries_add.append({"path": rel, "file": f"files/{rel}"})

    manifest = {
        "version": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "modified": entries_mod,
        "added": entries_add,
        "deleted": deleted,
    }
    save_json(content / "content.json", manifest)
    print(f"exported: {len(entries_mod)} modified, {len(entries_add)} added, "
          f"{len(deleted)} deleted -> {content}")
    return 0


def cmd_apply(args) -> int:
    xml = Path(args.xml)
    content = Path(args.content)
    manifest = load_json(content / "content.json")
    if not manifest:
        sys.exit(f"error: no {content}/content.json")
    ok = True
    for e in manifest.get("modified", []):
        if e.get("kind") == "full":
            if not args.dry_run:
                (xml / e["path"]).parent.mkdir(parents=True, exist_ok=True)
                # copyfile, not copy2: the copy must get a fresh mtime or
                # `make data` treats the older checked-out file as up to date.
                shutil.copyfile(content / e["file"], xml / e["path"])
            print(f"  copy {e['path']}")
            continue
        status, detail = apply_patch(xml, content / e["patch"], args.dry_run)
        if status == "conflict":
            print(f"  FAIL {e['path']}\n{detail}", file=sys.stderr)
            ok = False
        elif status == "already":
            print(f"  ok   {e['path']} (already applied)")
        else:
            print(f"  patch {e['path']}")
    for e in manifest.get("added", []):
        if not args.dry_run:
            (xml / e["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(content / e["file"], xml / e["path"])
        print(f"  add  {e['path']}")
    for rel in manifest.get("deleted", []):
        p = xml / rel
        if p.exists():
            if not args.dry_run:
                p.unlink()
            print(f"  del  {rel}")
    return 0 if ok else 1


def cmd_check(args) -> int:
    xml = Path(args.xml)
    content = Path(args.content)
    manifest = load_json(content / "content.json")
    if not manifest:
        sys.exit(f"error: no {content}/content.json")
    baseline = Baseline(Path(args.manifest), Path(args.original), Path(args.lcf2xml).resolve())
    problems = []
    try:
        for e in manifest.get("modified", []):
            rel = e["path"]
            if e.get("kind") == "full":
                if (content / e["file"]).read_bytes() != (xml / rel).read_bytes():
                    problems.append(f"{rel}: full file differs")
                continue
            old = baseline.path_for(rel)
            if old is None:
                problems.append(f"{rel}: cannot regenerate baseline")
                continue
            with tempfile.TemporaryDirectory(prefix="y2kki-check-") as td:
                tmp = Path(td)
                dst = tmp / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, dst)
                res = subprocess.run(
                    ["patch", "-p1", "--no-backup-if-mismatch", "-d", str(tmp)],
                    stdin=(content / e["patch"]).open("rb"),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0:
                    problems.append(f"{rel}: patch does not apply")
                elif dst.read_bytes() != (xml / rel).read_bytes():
                    problems.append(f"{rel}: patched result differs")
        for e in manifest.get("added", []):
            if (content / e["file"]).read_bytes() != (xml / e["path"]).read_bytes():
                problems.append(f"{e['path']}: added file differs")
        for rel in manifest.get("deleted", []):
            if (xml / rel).exists():
                problems.append(f"{rel}: should be deleted but exists")
    finally:
        baseline.cleanup()

    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    print(f"all {len(manifest.get('modified', [])) + len(manifest.get('added', []))} "
          f"content entries verified")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baseline", "status", "export", "apply", "check"])
    ap.add_argument("--xml", default="xml")
    ap.add_argument("--content", default="content")
    ap.add_argument("--manifest", default="build/manifest.json")
    ap.add_argument("--original", default="original")
    ap.add_argument("--lcf2xml", default="build/liblcf/lcf2xml")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.command == "baseline":
        return cmd_baseline(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "export":
        return cmd_export(args)
    if args.command == "apply":
        return cmd_apply(args)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())

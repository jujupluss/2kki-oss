#!/usr/bin/env python3
"""Generate EasyRPG Player translation catalogues (.po) from the XML data.

EasyRPG Player reads translations from ``language/<lang>/``:

  rpg_rt.ldb.po         database strings (context ``actors.1.name`` etc.)
  rpg_rt.ldb.common.po  common-event message boxes
  rpg_rt.ldb.battle.po  troop message boxes
  rpg_rt.lmt.po         map names
  Map0001.po            message boxes of one map
  meta.ini              language metadata

Message ``msgid`` is the *whole message box* (lines joined with ``\\n``), or
the choice list, matching ``Translation::RewriteEventCommandMessage`` in
``vendor/Player/src/translation.cpp``.

The glossary (``translation/glossary.json``) seeds translations:

  {"meta": {...},
   "terms":   {"gold": "Dreams", ...},        # keyed by Terms field
   "strings": {"はい": "Yes", ...}}           # keyed by source text

An existing translation in the same EasyRPG format can be imported with
``--import DIR`` (e.g. YNOproject's community translation, fetched with
``make fetch-yno``). Its entries are matched by context + msgid; by default
they win over the glossary, which fills the gaps (``--prefer local`` flips
that). Translated images in DIR (``Picture/`` etc.) are copied alongside.

Run ``make translate`` then ``make run-en``. A coverage report is written
to ``build/translation-report.md``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

# DB containers with per-object fields, and the fields worth translating.
CONTAINERS = [
    "actors", "classes", "skills", "items", "weapons", "armors", "enemies",
    "terrains", "attributes", "states", "animations", "chipsets",
]
FIELDS = {
    "name", "title", "description", "using_message1", "using_message2",
    "failure_message", "skill_name", "using_message",
    "easyrpg_battle2k3_message", "easyrpg_using_message",
    "message_actor", "message_enemy", "message_use", "message_affected",
    "message_recovery",
}
SKIP_VALUES = {"", "-", "default_term"}

# Event command codes (lcf::rpg::EventCommand::Code).
SHOW_MESSAGE = 10110
SHOW_MESSAGE_2 = 20110
SHOW_CHOICE = 10140
SHOW_CHOICE_OPTION = 20140
SHOW_CHOICE_END = 20141
CHANGE_HERO_NAME = 10610
CONDITIONAL_BRANCH = 12010
CHANGE_HERO_TITLE = 10620
MANIAC_SHOW_STRING_PICTURE = 3007


# ---------------------------------------------------------------------------
# PO writing
# ---------------------------------------------------------------------------
def po_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# Kana, kanji and half-width katakana: text a player would need translated.
_JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")


def needs_translation(text: str) -> bool:
    return bool(_JAPANESE.search(text))


# Names RPG Maker 2000 only shows in the editor: the map tree and the
# chipset/animation/terrain databases. They are still written (harmless),
# but not counted as text a player needs translated. YNO's community
# translation does not translate any of them either.
EDITOR_ONLY_FILES = {"rpg_rt.lmt.po"}
EDITOR_ONLY_CTX = {"chipsets.name", "animations.name", "terrains.name"}


# Yume 2kki's translation guidelines (wikiwiki.jp/yumenikki-g3, 決定事項 >
# 翻訳ガイドライン) say some text must stay in the original. Shown in the
# report so nobody "fills" these gaps; see notes/translation.md.
KEEP_ORIGINAL = {
    "Map0899.po": "wallpaper gallery: illustration titles and artist names "
                  "must stay untranslated",
}


def is_editor_only(po_name: str, ctx: str) -> bool:
    return po_name.lower() in EDITOR_ONLY_FILES or norm_ctx(ctx) in EDITOR_ONLY_CTX


def write_po(path: Path, entries: list[tuple[str, str]], translator: "Translator",
             lang: str = "en") -> dict:
    """Write one catalogue; return its counts (see ``empty_counts``)."""
    seen: set[tuple[str, str]] = set()
    counts = empty_counts()
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: yume2kki-oss\\n"',
        f'"Language: {lang}\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        "",
    ]
    for ctx, msgid in entries:
        key = (ctx, msgid)
        if key in seen:
            continue
        seen.add(key)
        # Player's PO reader treats a bare CR as a line break and has no
        # escape for it, so such a line would be a fatal parse error.
        if "\r" in msgid or "\r" in ctx:
            counts["skipped_cr"] += 1
            continue
        msgstr, source = translator.lookup(path.name, ctx, msgid)
        if "\r" in msgstr:
            msgstr, source = "", ""
            counts["skipped_cr"] += 1
        counts["entries"] += 1
        if msgstr:
            counts["translated"] += 1
            counts["by_source"][source] = counts["by_source"].get(source, 0) + 1
        if needs_translation(msgid):
            if is_editor_only(path.name, ctx):
                counts["editor_only"] += 1
            else:
                counts["japanese"] += 1
                if msgstr:
                    counts["japanese_translated"] += 1
                elif translator.import_left_empty(path.name, ctx, msgid):
                    counts["empty_in_import"] += 1
        if ctx:
            lines.append(f'msgctxt "{po_escape(ctx)}"')
        lines.append(f'msgid "{po_escape(msgid)}"')
        lines.append(f'msgstr "{po_escape(msgstr)}"')
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return counts


def empty_counts() -> dict:
    return {"entries": 0, "translated": 0, "japanese": 0,
            "japanese_translated": 0, "empty_in_import": 0, "editor_only": 0,
            "skipped_cr": 0, "by_source": {}}


def add_counts(total: dict, part: dict) -> None:
    for k, v in part.items():
        if k == "by_source":
            for src, n in v.items():
                total[k][src] = total[k].get(src, 0) + n
        else:
            total[k] += v


# ---------------------------------------------------------------------------
# PO reading / translation memory
# ---------------------------------------------------------------------------
_PO_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def po_unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _PO_ESCAPES.get(m.group(1), "\\" + m.group(1)), s)


def read_po(path: Path) -> list[tuple[str, str, str]]:
    """Return (msgctxt, msgid, msgstr) for every entry except the header.

    Handles multi-line strings (``msgid ""`` + continuation lines, as
    lcftrans writes them) and skips obsolete (``#~``) entries. ``#, fuzzy``
    translations are returned with an empty msgstr, as gettext treats them
    as untranslated. Plural forms are ignored (Player does not use them).
    """
    entries: list[tuple[str, str, str]] = []
    cur: dict[str, str] = {}
    cur_fuzzy = next_fuzzy = False
    field = ""

    def flush() -> None:
        if cur.get("msgid"):
            entries.append((cur.get("msgctxt", ""), cur["msgid"],
                            "" if cur_fuzzy else cur.get("msgstr", "")))
        cur.clear()

    for raw in path.read_text(encoding="utf-8-sig").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            # Comments only appear between entries: the previous one is done.
            flush()
            field = ""
            if line.startswith("#,") and "fuzzy" in line:
                next_fuzzy = True
            continue
        if line.startswith('"'):
            if field:
                cur[field] += po_unescape(line[1:-1])
            continue
        keyword, _, rest = line.partition(" ")
        if keyword not in ("msgctxt", "msgid", "msgstr"):
            field = ""
            continue
        # A new entry starts at msgctxt, or at msgid unless only its own
        # msgctxt has been read so far.
        if keyword == "msgctxt" or (keyword == "msgid" and "msgid" in cur):
            flush()
            cur_fuzzy, next_fuzzy = next_fuzzy, False
        elif keyword == "msgid" and not cur:
            cur_fuzzy, next_fuzzy = next_fuzzy, False
        field = keyword
        cur[field] = po_unescape(rest.strip()[1:-1])
    flush()
    return entries


_DB_CTX = re.compile(r"^([a-z0-9_]+)\.\d+\.([a-z0-9_]+)$")


def norm_ctx(ctx: str) -> str:
    """``actors.1.name`` -> ``actors.name`` (Player tries both, in that order)."""
    m = _DB_CTX.match(ctx)
    return f"{m.group(1)}.{m.group(2)}" if m else ctx


class TranslationMemory:
    """Existing translations (e.g. a community .po set), keyed by msgid.

    Lookup prefers the same catalogue file (``Map0042.po``), then the same
    context and msgid in any other file of the set: a message moved
    between maps or into a common event between game versions still
    matches. Text that changed between versions does not match at all;
    nothing is guessed.
    """

    def __init__(self) -> None:
        self.by_file: dict[tuple[str, str, str], str] = {}
        # (ctx, msgid) -> file key it first came from
        self.anywhere: dict[tuple[str, str], tuple[str, str, str]] = {}
        self.used: set[tuple[str, str, str]] = set()
        self.empty: set[tuple[str, str]] = set()   # present but untranslated
        self.files = 0

    def add_file(self, name: str, entries: list[tuple[str, str, str]]) -> None:
        self.files += 1
        fname = name.lower()
        for ctx, msgid, msgstr in entries:
            if not msgid:
                continue
            key = (norm_ctx(ctx), msgid)
            if not msgstr:
                self.empty.add(key)
                continue
            self.by_file[(fname, *key)] = msgstr
            self.anywhere.setdefault(key, (fname, *key))

    def load_dir(self, directory: Path) -> None:
        for po in sorted(directory.glob("*.po")):
            self.add_file(po.name, read_po(po))

    def lookup(self, po_name: str, ctx: str, msgid: str) -> tuple[str, str]:
        key = (norm_ctx(ctx), msgid)
        fkey = (po_name.lower(), *key)
        if fkey in self.by_file:
            self.used.add(fkey)
            return self.by_file[fkey], "import"
        if key in self.anywhere:
            origin = self.anywhere[key]
            self.used.add(origin)
            return self.by_file[origin], "import-moved"
        return "", ""

    def left_empty(self, ctx: str, msgid: str) -> bool:
        """The imported set has this text but did not translate it."""
        key = (norm_ctx(ctx), msgid)
        return key in self.empty and key not in self.anywhere

    def unused(self) -> int:
        """Imported translations this game version never asked for.

        A high number means the imported set targets a different game
        version (text that changed or was added after it)."""
        return len(self.by_file.keys() - self.used)


class Glossary:
    def __init__(self, path: Path, overrides: Path | None = None):
        data = {}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        self.meta = data.get("meta", {})
        self._messages = dict(data.get("messages", {}))
        if overrides and overrides.is_file():
            extra = json.loads(overrides.read_text(encoding="utf-8"))
            self._messages.update(extra.get("messages", {}))
        self._terms = {
            k.strip(" \u3000"): v for k, v in data.get("terms", {}).items()
        }
        self._strings = {
            k.strip(" \u3000"): v for k, v in data.get("strings", {}).items()
        }

    def term(self, field: str) -> str:
        return self._terms.get(field, "")

    def lookup(self, ctx: str, msgid: str) -> tuple[str, str]:
        """(translation, source) from our own glossary/overrides."""
        if ctx.startswith("terms."):
            t = self.term(ctx.split(".", 1)[1])
            return (t, "glossary") if t else ("", "")
        if msgid in self._messages:
            return self._messages[msgid], "override"
        t = self.string(msgid)
        return (t, "glossary") if t else ("", "")

    def string(self, msgid: str) -> str:
        # Exact whole-message override wins (used for control-code menus).
        if msgid in self._messages:
            return self._messages[msgid]
        key = msgid.strip(" \u3000")
        if key in self._strings:
            return self._strings[key]
        # Multi-line message boxes / choice lists: translate line by line when
        # every line is a known phrase (e.g. "はい\nいいえ" -> "Yes\nNo").
        if "\n" in msgid:
            lines = msgid.split("\n")
            out = []
            for line in lines:
                k = line.strip(" \u3000")
                if k and k not in self._strings:
                    return ""
                out.append(self._strings.get(k, line))
            return "\n".join(out)
        return ""


class Translator:
    """Combine imported translations with our glossary/overrides.

    ``prefer="import"`` (default): an imported translation wins and ours
    fill the gaps, so the result reads like the imported set throughout
    (names, terminology). ``prefer="local"`` lets our curated entries win.
    """

    def __init__(self, glossary: Glossary, memory: TranslationMemory | None = None,
                 prefer: str = "import"):
        self.glossary = glossary
        self.memory = memory
        self.prefer = prefer

    def lookup(self, po_name: str, ctx: str, msgid: str) -> tuple[str, str]:
        local = self.glossary.lookup(ctx, msgid)
        if self.memory is None:
            return local
        imported = self.memory.lookup(po_name, ctx, msgid)
        first, second = (imported, local) if self.prefer == "import" else (local, imported)
        return first if first[0] else second

    def import_left_empty(self, po_name: str, ctx: str, msgid: str) -> bool:
        return self.memory is not None and self.memory.left_empty(ctx, msgid)


# ---------------------------------------------------------------------------
# database strings
# ---------------------------------------------------------------------------
def db_entries(db: ET.Element) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for container in CONTAINERS:
        el = db.find(container)
        if el is None:
            continue
        for idx, obj in enumerate(list(el), 1):
            for field in obj:
                if field.tag not in FIELDS:
                    continue
                value = field.text or ""
                if value.strip() in SKIP_VALUES:
                    continue
                out.append((f"{container}.{idx}.{field.tag}", value))

    terms = db.find("terms/Terms")
    if terms is not None:
        for field in terms:
            value = field.text or ""
            if value.strip() in SKIP_VALUES:
                continue
            out.append((f"terms.{field.tag}", value))
    return out


# ---------------------------------------------------------------------------
# event messages
# ---------------------------------------------------------------------------
def commands_of(container: ET.Element) -> list[tuple[int, str, int, list[int]]]:
    cmds = []
    for ec in container.iter("EventCommand"):
        try:
            code = int(ec.findtext("code", "0") or 0)
        except ValueError:
            code = 0
        s = ec.findtext("string", "") or ""
        try:
            indent = int(ec.findtext("indent", "0") or 0)
        except ValueError:
            indent = 0
        params: list[int] = []
        for x in (ec.findtext("parameters", "") or "").split():
            try:
                params.append(int(x))
            except ValueError:
                params.append(0)
        cmds.append((code, s, indent, params))
    return cmds


def message_units(cmds) -> list[tuple[str, str]]:
    """Return (context, msgid) pairs exactly as Player looks them up."""
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(cmds):
        code, s, indent, params = cmds[i]

        if code == SHOW_MESSAGE:
            lines = [s]
            i += 1
            while i < len(cmds) and cmds[i][0] == SHOW_MESSAGE_2:
                lines.append(cmds[i][1])
                i += 1
            out.append(("", "\n".join(lines)))
            continue

        if code == SHOW_CHOICE:
            opts = []
            j = i + 1
            # Player uses the indent of the first command after ShowChoice.
            opt_indent = cmds[j][2] if j < len(cmds) else indent
            while j < len(cmds):
                c = cmds[j]
                if c[2] == opt_indent:
                    if c[0] == SHOW_CHOICE_OPTION and (c[3][0] if c[3] else 0) < 4:
                        opts.append(c[1])
                    if c[0] == SHOW_CHOICE_END:
                        break
                j += 1
            if opts:
                out.append(("", "\n".join(opts)))
            i += 1
            continue

        if code == CHANGE_HERO_NAME or (
            code == CONDITIONAL_BRANCH and len(params) > 2
            and params[0] == 5 and params[2] == 1
        ):
            if s:
                out.append(("actors.name", s))
        elif code == CHANGE_HERO_TITLE:
            if s:
                out.append(("actors.title", s))
        elif code == MANIAC_SHOW_STRING_PICTURE:
            if s.startswith("\x01"):
                end = len(s)
                for k in range(1, len(s)):
                    if s[k] in "\x01\x02\x03":
                        end = k
                        break
                term = s[1:end].replace("\r\n", "\n")
                if term:
                    out.append(("strpic", term))
        i += 1
    return out


# ---------------------------------------------------------------------------
# imports: metadata, assets, report
# ---------------------------------------------------------------------------
def read_meta(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                out[key.strip()] = value.strip()
    return out


def copy_translated_assets(imports: list[Path], out: Path) -> int:
    """Copy translated images (Picture/, Title/, ...) next to the catalogues.

    Player looks for an asset in the active language directory before the
    game directory (FileFinder find_generic), so these replace the
    Japanese images in-game. Earlier import dirs win.
    """
    subdirs = {p.name for d in imports for p in d.iterdir() if p.is_dir()}
    for name in subdirs:                 # drop stale copies from older runs
        shutil.rmtree(out / name, ignore_errors=True)
    copied = 0
    for d in reversed(imports):          # later first, so earlier overwrite
        for src in sorted(p for p in d.rglob("*") if p.is_file()):
            rel = src.relative_to(d)
            if len(rel.parts) < 2:       # top-level files are catalogues/meta
                continue
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            copied += 1
    return copied


def pct(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else 100.0


def short(c: dict) -> str:
    return (f"{c['entries']} entries, {c['japanese_translated']}/{c['japanese']} "
            "Japanese lines translated")


def write_report(path: Path, stats: dict) -> None:
    """Human summary: coverage, sources, and where the gaps are."""
    t = stats["totals"]
    lines = [
        "# Translation report",
        "",
        f"Generated: {stats['generated']}, language `{stats['lang']}`, "
        f"prefer `{stats['prefer']}`",
        "",
        f"- imports: {', '.join(f'`{d}`' for d in stats['imports']) or '_none_'}",
        f"- entries: **{t['entries']}**; player-facing entries containing "
        f"Japanese: **{t['japanese']}**",
        f"- of those translated: **{t['japanese_translated']}** "
        f"(**{pct(t['japanese_translated'], t['japanese']):.1f}%**)",
        f"- untranslated, and also left untranslated by the imported set: "
        f"**{t['empty_in_import']}**",
        f"- untranslated, not in the imported set at all: "
        f"**{t['japanese'] - t['japanese_translated'] - t['empty_in_import']}**",
        f"- editor-only names not counted (map tree, chipsets, animations, "
        f"terrains): {t['editor_only']}",
        f"- by source: {', '.join(f'{k} {v}' for k, v in sorted(t['by_source'].items())) or '_none_'}",
        f"- imported translations unused (not in this game version): "
        f"**{stats['imported_unused']}**",
        f"- skipped (raw CR, unparseable by Player): {t['skipped_cr']}",
        "",
        "## Biggest gaps",
        "",
        "Catalogues with the most untranslated Japanese entries.",
        "",
        "| file | untranslated | Japanese | coverage | note |",
        "|---|---:|---:|---:|---|",
    ]
    gaps = sorted(((n, c) for n, c in stats["files"].items()
                   if c["japanese"] > c["japanese_translated"]),
                  key=lambda nc: -(nc[1]["japanese"] - nc[1]["japanese_translated"]))
    for name, c in gaps[:40]:
        lines.append(f"| `{name}` | {c['japanese'] - c['japanese_translated']} "
                     f"| {c['japanese']} | {pct(c['japanese_translated'], c['japanese']):.0f}% "
                     f"| {KEEP_ORIGINAL.get(name, '')} |")
    if len(gaps) > 40:
        lines.append(f"| … {len(gaps) - 40} more | | | | |")
    if not gaps:
        lines.append("| _none_ | | | | |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", default="xml")
    ap.add_argument("--out", default="translation")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--glossary", default="translation/glossary.json")
    ap.add_argument("--overrides", default="translation/overrides.json")
    ap.add_argument("--maps-limit", type=int, default=0)
    ap.add_argument("--no-maps", action="store_true")
    ap.add_argument("--stats", default="build/translation-stats.json")
    ap.add_argument("--report", default="build/translation-report.md")
    ap.add_argument("--import", dest="imports", action="append", default=[],
                    metavar="DIR",
                    help="existing EasyRPG translation (a dir of .po files, e.g. "
                         "external/ynotranslations/2kki/en) to reuse; repeatable, "
                         "earlier dirs win")
    ap.add_argument("--prefer", choices=["import", "local"], default="import",
                    help="which wins when both an imported and a local "
                         "(glossary/overrides) translation exist")
    args = ap.parse_args()

    xml = Path(args.xml)
    out = Path(args.out) / args.lang
    glossary = Glossary(Path(args.glossary), Path(args.overrides))
    t0 = time.time()

    if not xml.is_dir():
        sys.exit(f"error: XML dir not found: {xml}")
    out.mkdir(parents=True, exist_ok=True)

    imports = [Path(d) for d in args.imports]
    memory = None
    if imports:
        memory = TranslationMemory()
        for d in imports:
            if not d.is_dir():
                sys.exit(f"error: import dir not found: {d}")
            memory.load_dir(d)
        print(f"  import  {memory.files} catalogue(s), "
              f"{len(memory.by_file)} translations from {', '.join(map(str, imports))}")
    translator = Translator(glossary, memory, prefer=args.prefer)

    meta = glossary.meta
    lang_code = meta.get("code", args.lang)
    description = meta.get("description", "")
    for d in imports:
        # Keep the imported set's credit line (e.g. "Localization by ...").
        imported_desc = read_meta(d / "meta.ini").get("Description", "")
        if imported_desc:
            description = imported_desc
            break
    (out / "meta.ini").write_text(
        "[Language]\n"
        f"Name={meta.get('name', 'English')}\n"
        f"Description={description}\n"
        f"Code={lang_code}\n"
        f"Term={meta.get('term', 'Language')}\n"
        f"GameTitle={meta.get('game_title', 'Yume 2kki')}\n",
        encoding="utf-8",
    )
    copied = copy_translated_assets(imports, out)
    if copied:
        print(f"  assets  {copied} translated image(s)")

    totals = empty_counts()
    totals["files"] = 0
    per_file: dict[str, dict] = {}

    def emit(name: str, entries):
        counts = write_po(out / name, entries, translator, lang=lang_code)
        totals["files"] += 1
        add_counts(totals, counts)
        per_file[name] = counts

    # ---- database --------------------------------------------------------
    db = ET.parse(xml / "RPG_RT.edb").getroot().find("Database")
    if db is None:
        sys.exit("error: no <Database> in RPG_RT.edb")
    emit("rpg_rt.ldb.po", db_entries(db))
    print(f"  db      {short(per_file['rpg_rt.ldb.po'])}")

    ce = db.find("commonevents")
    if ce is not None:
        entries = []
        for ev in ce:
            entries += message_units(commands_of(ev))
        emit("rpg_rt.ldb.common.po", entries)
        print(f"  common  {short(per_file['rpg_rt.ldb.common.po'])}")

    troops = db.find("troops")
    if troops is not None:
        entries = []
        for troop in troops:
            for page in troop.findall("pages/TroopPage"):
                entries += message_units(commands_of(page))
        emit("rpg_rt.ldb.battle.po", entries)
        print(f"  battle  {short(per_file['rpg_rt.ldb.battle.po'])}")

    # ---- map names -------------------------------------------------------
    lmt = ET.parse(xml / "RPG_RT.emt").getroot()
    entries = []
    for mi in lmt.iter("MapInfo"):
        name = mi.findtext("name", "")
        if name:
            entries.append(("", name))
    emit("rpg_rt.lmt.po", entries)
    print(f"  lmt     {short(per_file['rpg_rt.lmt.po'])}")

    # ---- map messages ----------------------------------------------------
    if not args.no_maps:
        maps = sorted(xml.glob("Map*.emu"))
        if args.maps_limit:
            maps = maps[: args.maps_limit]
        for i, path in enumerate(maps, 1):
            root = ET.parse(path).getroot()
            entries = []
            for page in root.iter("EventPage"):
                entries += message_units(commands_of(page))
            emit(path.stem + ".po", entries)
            if i % 500 == 0:
                print(f"  maps    {i}/{len(maps)} ({time.time() - t0:.1f}s)")
        print(f"  maps    {len(maps)} files")

    unused = memory.unused() if memory else 0
    stats = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "lang": args.lang,
        "imports": [str(d) for d in imports],
        "prefer": args.prefer,
        "imported_unused": unused,
        "totals": totals,
        "files": per_file,
    }
    Path(args.stats).parent.mkdir(parents=True, exist_ok=True)
    Path(args.stats).write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    write_report(Path(args.report), stats)

    print()
    print(f"entries    {totals['entries']} ({totals['japanese']} player-facing with "
          f"Japanese, {totals['editor_only']} editor-only names not counted)")
    print(f"translated {totals['japanese_translated']}/{totals['japanese']} "
          f"player-facing Japanese entries "
          f"({pct(totals['japanese_translated'], totals['japanese']):.1f}%)")
    if memory:
        print(f"           {totals['empty_in_import']} more are left untranslated "
              "by the imported set too")
    print(f"sources    {totals['by_source']}")
    if memory:
        print(f"imported   {unused} translation(s) unused (text not in this game version)")
    if totals["skipped_cr"]:
        print(f"skipped    {totals['skipped_cr']} entr(y/ies) with a raw CR (Player cannot parse)")
    print(f"files      {totals['files']} in {out}")
    print(f"report     {args.report}")
    print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

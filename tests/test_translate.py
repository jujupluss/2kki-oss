import json

from conftest import load_script

translate = load_script("translate")

MSG, MSG2 = translate.SHOW_MESSAGE, translate.SHOW_MESSAGE_2
CHOICE, OPT, END = (translate.SHOW_CHOICE, translate.SHOW_CHOICE_OPTION,
                    translate.SHOW_CHOICE_END)


def test_message_box_lines_are_joined():
    cmds = [(MSG, "line 1", 0, []), (MSG2, "line 2", 0, []), (MSG, "next", 0, [])]
    assert translate.message_units(cmds) == [("", "line 1\nline 2"), ("", "next")]


def test_choice_options_are_joined_and_cancel_branch_skipped():
    cmds = [
        (CHOICE, "", 0, []),
        (OPT, "はい", 0, [0]),
        (MSG, "nested", 1, []),
        (OPT, "いいえ", 0, [1]),
        (OPT, "", 0, [4]),        # cancel branch, not shown to the player
        (END, "", 0, []),
    ]
    units = translate.message_units(cmds)
    assert units[0] == ("", "はい\nいいえ")


def test_glossary_line_by_line_and_overrides(tmp_path):
    g = tmp_path / "glossary.json"
    g.write_text(json.dumps({"strings": {"はい": "Yes", "いいえ": "No"},
                             "terms": {"gold": "Dreams"}}))
    o = tmp_path / "overrides.json"
    o.write_text(json.dumps({"messages": {"はい\nいいえ": "Sure\nNope"}}))
    gl = translate.Glossary(g, o)
    assert gl.string("　はい ") == "Yes"
    assert gl.string("はい\nいいえ") == "Sure\nNope"     # override wins
    assert gl.string("いいえ\nはい") == "No\nYes"       # line by line
    assert gl.string("いいえ\n未知") == ""             # all-or-nothing
    assert gl.term("gold") == "Dreams"


def test_po_escape():
    assert translate.po_escape('a"b\\c\nd') == 'a\\"b\\\\c\\nd'


def test_committed_glossary_and_overrides_parse():
    from conftest import REPO
    gl = translate.Glossary(REPO / "translation" / "glossary.json",
                            REPO / "translation" / "overrides.json")
    assert gl.term("yes") == "Yes"


def test_po_header_uses_the_language_code(tmp_path):
    gl = translate.Glossary(tmp_path / "none.json")
    po = tmp_path / "x.po"
    translate.write_po(po, [("", "はい")], translate.Translator(gl), lang="de")
    assert '"Language: de\\n"' in po.read_text()


PO_SAMPLE = r'''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"

#. ID 6, Page 2
msgctxt "actors.name"
msgid "うろつき"
msgstr "Urotsuki"

#. lcftrans multi-line style
msgid ""
"\\>一行目\n"
"二行目 \"引用\""
msgstr ""
"\\>Line one\n"
"Line two \"quoted\""

msgid "未訳"
msgstr ""

#, fuzzy
msgid "あいまい"
msgstr "Fuzzy guess"

#~ msgid "古い"
#~ msgstr "Obsolete"
msgid "最後"
msgstr "Last"
'''


def test_read_po(tmp_path):
    po = tmp_path / "Map0001.po"
    po.write_text(PO_SAMPLE, encoding="utf-8")
    assert translate.read_po(po) == [
        ("actors.name", "うろつき", "Urotsuki"),
        ("", '\\>一行目\n二行目 "引用"', '\\>Line one\nLine two "quoted"'),
        ("", "未訳", ""),
        ("", "あいまい", ""),
        ("", "最後", "Last"),
    ]


def test_read_po_round_trips_what_write_po_writes(tmp_path):
    gl = translate.Glossary(tmp_path / "none.json")
    po = tmp_path / "x.po"
    entries = [("", 'a\\b "c"\nd'), ("skills.3.name", "技")]
    translate.write_po(po, entries, translate.Translator(gl))
    assert [(c, m) for c, m, _s in translate.read_po(po)] == entries


def test_norm_ctx():
    assert translate.norm_ctx("actors.12.name") == "actors.name"
    assert translate.norm_ctx("terms.gold") == "terms.gold"
    assert translate.norm_ctx("actors.name") == "actors.name"
    assert translate.norm_ctx("") == ""


def test_translation_memory_prefers_same_file_then_moved():
    tm = translate.TranslationMemory()
    tm.add_file("RPG_RT.ldb.po", [("actors.name", "うろつき", "Urotsuki")])
    tm.add_file("Map0002.po", [("", "はい", "Yes (map 2)"), ("", "空", "")])
    tm.add_file("Map0005.po", [("", "はい", "Yes (map 5)"), ("", "移動", "Moved")])
    # Indexed DB context, case-insensitive catalogue name.
    assert tm.lookup("rpg_rt.ldb.po", "actors.1.name", "うろつき") == ("Urotsuki", "import")
    assert tm.lookup("Map0005.po", "", "はい") == ("Yes (map 5)", "import")
    # Message that sits in another map in the imported version.
    assert tm.lookup("Map0002.po", "", "移動") == ("Moved", "import-moved")
    # Empty translations are not memory.
    assert tm.lookup("Map0002.po", "", "空") == ("", "")
    assert tm.unused() == 1      # Map0002 "はい" was never asked for


# ---------------------------------------------------------------------------
# end to end: translate.py on a synthetic game with an imported .po set
# ---------------------------------------------------------------------------
def cmd(code, string="", indent=0, params="0"):
    return (f"<EventCommand><code>{code}</code><indent>{indent}</indent>"
            f"<string>{string}</string><parameters>{params}</parameters></EventCommand>")


def make_game_xml(xml):
    from conftest import write
    write(xml / "RPG_RT.edb", (
        "<LDB><Database>"
        "<actors><Actor id=\"0001\"><name>うろつき</name></Actor></actors>"
        "<terms><Terms><gold>夢</gold><yes>はい</yes></Terms></terms>"
        "<commonevents><CommonEvent id=\"0001\">"
        + cmd(MSG, "共通のメッセージ") +
        "</CommonEvent></commonevents>"
        "</Database></LDB>"))
    write(xml / "RPG_RT.emt", "<LMT><TreeMap><maps><MapInfo id=\"0001\"><name>MAP0001 夢</name>"
          "</MapInfo></maps></TreeMap></LMT>")
    write(xml / "Map0001.emu", (
        "<LMU><Map><events><Event><pages><EventPage><event_commands>"
        + cmd(MSG, "こんにちは") + cmd(MSG, "移動した文")
        + cmd(MSG, "訳なし") + cmd(MSG, "空欄") + cmd(MSG, "\\&gt;\\&gt;") + cmd(MSG, "改行&#xD;")
        + "</event_commands></EventPage></pages></Event></events></Map></LMU>"))


def make_import(d):
    from conftest import write
    write(d / "meta.ini", "[Language]\nName=English\nDescription=Localization by Someone\n")
    write(d / "RPG_RT.ldb.po",
          'msgid ""\nmsgstr ""\n\nmsgctxt "actors.name"\nmsgid "うろつき"\nmsgstr "Urotsuki"\n\n'
          'msgctxt "terms.gold"\nmsgid "夢"\nmsgstr "Dreams (YNO)"\n')
    write(d / "Map0001.po", 'msgid "こんにちは"\nmsgstr "Hello"\n\nmsgid "空欄"\nmsgstr ""\n')
    write(d / "Map0099.po", 'msgid "移動した文"\nmsgstr "Moved line"\n\n'
          'msgid "消えた文"\nmsgstr "Text from a newer version"\n')
    write(d / "Picture" / "title_en.png", b"PNG")


def read_out(out, name):
    return {(c, m): t for c, m, t in translate.read_po(out / name)}


def run_translate(tmp_path, *extra):
    from conftest import run_script, write
    write(tmp_path / "glossary.json", json.dumps(
        {"terms": {"gold": "Dreams (ours)", "yes": "Yes"}, "strings": {"はい": "Yes", "訳なし": "Ours"}}))
    return run_script("translate", "--xml", "xml", "--out", "tr", "--lang", "en",
                      "--glossary", "glossary.json", "--overrides", "none.json",
                      "--stats", "stats.json", "--report", "report.md", *extra,
                      cwd=tmp_path)


def test_translate_imports_existing_po_set(tmp_path):
    make_game_xml(tmp_path / "xml")
    make_import(tmp_path / "yno")
    run_translate(tmp_path, "--import", "yno")
    out = tmp_path / "tr" / "en"

    db = read_out(out, "rpg_rt.ldb.po")
    assert db[("actors.1.name", "うろつき")] == "Urotsuki"       # normalised ctx
    assert db[("terms.gold", "夢")] == "Dreams (YNO)"            # import wins
    assert db[("terms.yes", "はい")] == "Yes"                    # ours fills gap

    m = read_out(out, "Map0001.po")
    assert m[("", "こんにちは")] == "Hello"
    assert m[("", "移動した文")] == "Moved line"                 # found in Map0099
    assert m[("", "訳なし")] == "Ours"
    assert ("", "改行\r") not in m                               # raw CR skipped

    assert (out / "Picture" / "title_en.png").read_bytes() == b"PNG"
    assert "Description=Localization by Someone" in (out / "meta.ini").read_text()

    stats = json.loads((tmp_path / "stats.json").read_text())
    assert stats["imported_unused"] == 1                          # 消えた文
    assert stats["totals"]["skipped_cr"] == 1
    assert stats["totals"]["by_source"] == {"import": 3, "import-moved": 1, "glossary": 2}
    t = stats["totals"]
    assert t["editor_only"] == 1                    # "MAP0001 夢" map-tree name
    assert t["empty_in_import"] == 1                # 空欄: YNO has it, untranslated
    # うろつき 夢 はい 共通のメッセージ こんにちは 移動した文 訳なし 空欄
    assert (t["japanese"], t["japanese_translated"]) == (8, 6)
    assert "Biggest gaps" in (tmp_path / "report.md").read_text()


def test_translate_prefer_local(tmp_path):
    make_game_xml(tmp_path / "xml")
    make_import(tmp_path / "yno")
    run_translate(tmp_path, "--import", "yno", "--prefer", "local")
    db = read_out(tmp_path / "tr" / "en", "rpg_rt.ldb.po")
    assert db[("terms.gold", "夢")] == "Dreams (ours)"
    assert db[("actors.1.name", "うろつき")] == "Urotsuki"       # no local entry


def test_translate_without_import_still_works(tmp_path):
    make_game_xml(tmp_path / "xml")
    run_translate(tmp_path)
    db = read_out(tmp_path / "tr" / "en", "rpg_rt.ldb.po")
    assert db[("terms.gold", "夢")] == "Dreams (ours)"
    assert db[("actors.1.name", "うろつき")] == ""


def test_read_po_context_entry_followed_by_plain_entry(tmp_path):
    # No comment or blank line between entries (our own writer's style
    # has blank lines, but entries may also follow directly).
    po = tmp_path / "x.po"
    po.write_text('msgctxt "actors.name"\nmsgid "名前"\nmsgstr ""\n'
                  'msgid "本文"\nmsgstr "Body"\n'
                  '#, fuzzy\nmsgctxt "c"\nmsgid "曖昧"\nmsgstr "Guess"\n'
                  'msgid "次"\nmsgstr "Next"\n', encoding="utf-8")
    assert translate.read_po(po) == [
        ("actors.name", "名前", ""), ("", "本文", "Body"), ("c", "曖昧", ""), ("", "次", "Next")]


def test_read_po_round_trips_a_real_sized_catalogue(tmp_path):
    gl = translate.Glossary(tmp_path / "none.json")
    entries = [(("actors.name" if i % 3 == 0 else ""), f"文{i}") for i in range(300)]
    po = tmp_path / "Map0001.po"
    translate.write_po(po, entries, translate.Translator(gl))
    assert [(c, m) for c, m, _s in translate.read_po(po)] == entries

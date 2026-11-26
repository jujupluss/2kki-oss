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
    translate.write_po(po, [("", "はい")], gl, lang="de")
    assert '"Language: de\\n"' in po.read_text()

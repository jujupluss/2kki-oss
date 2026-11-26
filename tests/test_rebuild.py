from conftest import age, run_script


def make_data(work, lcf2xml):
    return run_script("rebuild", "--lcf2xml", lcf2xml, cwd=work)


def test_fresh_extract_needs_no_rebuild(extracted, lcf2xml):
    res = make_data(extracted, lcf2xml)
    assert "XML files to rebuild: 0" in res.stdout


def test_edit_is_rebuilt(extracted, lcf2xml):
    game, xml = extracted / "build" / "game", extracted / "xml"
    age(game / "Map0001.lmu", 10)
    (xml / "Map0001.emu").write_bytes(b"EDITED")
    make_data(extracted, lcf2xml)
    assert (game / "Map0001.lmu").read_bytes() == b"converted:EDITED"


def test_edit_survives_re_extract(extracted, game_src, lcf2xml):
    game, xml = extracted / "build" / "game", extracted / "xml"
    (xml / "Map0001.emu").write_bytes(b"EDITED")
    age(game / "Map0001.lmu", 10)
    make_data(extracted, lcf2xml)

    # Re-extract copies the original LCF back but keeps the (edited) XML.
    run_script("extract", "--src", game_src, "--lcf2xml", lcf2xml,
               "--report", "build/r.md", cwd=extracted)
    assert (xml / "Map0001.emu").read_bytes() == b"EDITED"

    make_data(extracted, lcf2xml)
    assert (game / "Map0001.lmu").read_bytes() == b"converted:EDITED"


def test_xml_only_refresh_leaves_lcf_fresh(extracted, game_src, lcf2xml):
    run_script("extract", "--xml-only", "--force-xml", "--src", game_src,
               "--lcf2xml", lcf2xml, cwd=extracted)
    res = make_data(extracted, lcf2xml)
    assert "XML files to rebuild: 0" in res.stdout

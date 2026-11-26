import xml.etree.ElementTree as ET
from pathlib import Path

from conftest import load_script, run_script, write

map_tree = load_script("map_tree")


def mapinfo(mid, parent, mtype=1, music_type=0, music="", bg_type=0):
    return (f'<MapInfo id="{mid:04d}"><name>MAP{mid:04d}</name>'
            f"<parent_map>{parent}</parent_map><indentation>0</indentation>"
            f"<type>{mtype}</type><music_type>{music_type}</music_type>"
            f"<music><Music><name>{music}</name></Music></music>"
            f"<background_type>{bg_type}</background_type>"
            f"<background_name></background_name></MapInfo>")


def make_tree(path: Path, nodes: list[str]) -> Path:
    return write(path, '<?xml version="1.0" encoding="UTF-8"?>\n<LMT><TreeMap><maps>'
                 + "".join(nodes) + "</maps></TreeMap></LMT>\n")


def test_contiguous_ranges():
    assert map_tree.contiguous_ranges([1, 2, 3, 5, 7, 8]) == ["1-3", "5", "7-8"]
    assert map_tree.contiguous_ranges([]) == []


def test_prune_reparents_and_materialises_music(tmp_path):
    # 0 root -> 1 (kept) -> 2 (dangling, plays "song") -> 3 (kept, inherits)
    emt = make_tree(tmp_path / "xml" / "RPG_RT.emt", [
        mapinfo(0, 0, mtype=0),
        mapinfo(1, 0, music_type=2),
        mapinfo(2, 1, music_type=2, music="song"),
        mapinfo(3, 2),
    ])
    for i in (1, 3):
        write(tmp_path / "game" / f"Map{i:04d}.lmu", b"x")
    run_script("map_tree", "--lmt", emt, "--game", "game", "--apply",
               "--backup", "build/bak.emt", cwd=tmp_path)

    maps = {int(el.get("id")): el for el in ET.parse(emt).getroot().iter("MapInfo")}
    assert sorted(maps) == [0, 1, 3]
    assert maps[3].findtext("parent_map") == "1"
    assert maps[3].findtext("indentation") == "2"
    assert maps[3].findtext("music_type") == "2"
    assert maps[3].findtext("music/Music/name") == "song"


def test_second_prune_keeps_the_original_backup(tmp_path):
    emt = make_tree(tmp_path / "xml" / "RPG_RT.emt", [
        mapinfo(0, 0, mtype=0), mapinfo(1, 0), mapinfo(2, 1)])
    original = emt.read_bytes()
    write(tmp_path / "game" / "Map0001.lmu", b"x")
    for _ in range(2):
        run_script("map_tree", "--lmt", emt, "--game", "game", "--apply",
                   "--backup", "build/bak.emt", cwd=tmp_path)
    assert (tmp_path / "build" / "bak.emt").read_bytes() == original


def test_area_nodes_are_kept_with_their_map(tmp_path):
    # Areas (type 2) never have a Map*.lmu; they belong to their parent map.
    emt = make_tree(tmp_path / "xml" / "RPG_RT.emt", [
        mapinfo(0, 0, mtype=0),
        mapinfo(1, 0),
        mapinfo(2, 1, mtype=2),      # area on a kept map
        mapinfo(3, 0),               # dangling map
        mapinfo(4, 3, mtype=2),      # area on the dangling map
    ])
    write(tmp_path / "game" / "Map0001.lmu", b"x")
    run_script("map_tree", "--lmt", emt, "--game", "game", "--apply",
               "--backup", "build/bak.emt", cwd=tmp_path)
    ids = sorted(int(el.get("id")) for el in ET.parse(emt).getroot().iter("MapInfo"))
    assert ids == [0, 1, 2]

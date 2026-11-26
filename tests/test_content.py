import difflib
import json
from pathlib import Path

from conftest import age, run_script, snapshot, write


def make_content(work: Path, modified=(), added=()) -> Path:
    content = work / "content"
    write(content / "content.json", json.dumps(
        {"modified": list(modified), "added": list(added), "deleted": []}))
    return content


def make_patch(work: Path, rel: str, old: str, new: str) -> dict:
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    write(work / "content" / "patches" / f"{rel}.patch", diff)
    return {"path": rel, "patch": f"patches/{rel}.patch", "kind": "patch"}


def apply(work: Path, *extra, check=True):
    return run_script("content", "apply", *extra, cwd=work, check=check)


def test_full_file_entry_reaches_the_game(extracted, lcf2xml):
    # The committed full file is older than the freshly extracted LCF
    # (git checkout happened before `make extract`).
    full = write(extracted / "content" / "files" / "Map0001.emu", b"OUR MAP")
    age(full, 7200)
    make_content(extracted, modified=[
        {"path": "Map0001.emu", "file": "files/Map0001.emu", "kind": "full"}])
    apply(extracted)
    run_script("rebuild", "--lcf2xml", lcf2xml, cwd=extracted)
    game = extracted / "build" / "game"
    assert (game / "Map0001.lmu").read_bytes() == b"converted:OUR MAP"


def test_apply_twice_is_a_no_op(tmp_path):
    write(tmp_path / "xml" / "Map0002.emu", "one\ntwo\nthree\n")
    entry = make_patch(tmp_path, "Map0002.emu", "one\ntwo\nthree\n", "one\nTWO\nthree\n")
    make_content(tmp_path, modified=[entry])

    apply(tmp_path)
    after_first = snapshot(tmp_path / "xml")
    assert after_first == {"Map0002.emu": b"one\nTWO\nthree\n"}

    res = apply(tmp_path, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "already applied" in res.stdout
    assert snapshot(tmp_path / "xml") == after_first     # no .rej, no revert


def test_conflicting_patch_fails_without_touching_xml(tmp_path):
    write(tmp_path / "xml" / "Map0002.emu", "something\nelse\nentirely\n")
    entry = make_patch(tmp_path, "Map0002.emu", "one\ntwo\nthree\n", "one\nTWO\nthree\n")
    make_content(tmp_path, modified=[entry])
    before = snapshot(tmp_path / "xml")
    res = apply(tmp_path, check=False)
    assert res.returncode == 1
    assert "FAIL Map0002.emu" in res.stderr
    assert snapshot(tmp_path / "xml") == before


def test_dry_run_changes_nothing(tmp_path):
    write(tmp_path / "xml" / "Map0001.emu", "base\n")
    write(tmp_path / "xml" / "Map0002.emu", "one\ntwo\n")
    write(tmp_path / "content" / "files" / "Map0001.emu", "full\n")
    write(tmp_path / "content" / "files" / "Map9999.emu", "new\n")
    entry = make_patch(tmp_path, "Map0002.emu", "one\ntwo\n", "one\nTWO\n")
    make_content(tmp_path, modified=[
        entry, {"path": "Map0001.emu", "file": "files/Map0001.emu", "kind": "full"}],
        added=[{"path": "Map9999.emu", "file": "files/Map9999.emu"}])
    before = snapshot(tmp_path / "xml")
    apply(tmp_path, "--dry-run")
    assert snapshot(tmp_path / "xml") == before


def roundtrip_diff(tmp_path, old: str, new: str) -> str:
    from conftest import load_script
    import subprocess
    content = load_script("content")
    a = write(tmp_path / "a" / "Map.emu", old.encode())
    b = write(tmp_path / "b" / "Map.emu", new.encode())
    patch = write(tmp_path / "Map.patch", content.diff_files(a, b, "Map.emu").encode())
    subprocess.run(["patch", "-p1", "-s", "-d", str(tmp_path / "a"), "-i", str(patch)],
                   check=True, stdin=subprocess.DEVNULL)
    return a.read_bytes().decode()


def test_diff_keeps_unicode_line_separators_inside_lines(tmp_path):
    # str.splitlines() would also split on these; patch only splits on \n.
    old = "<s>a b</s>\n<s>c\x1cd</s>\n<s>e\x0cf</s>\n"
    new = old.replace("c\x1cd", "C\x1cD")
    assert roundtrip_diff(tmp_path, old, new) == new


def test_diff_handles_missing_final_newline(tmp_path):
    old = "one\ntwo"
    new = "one\nTWO"
    assert roundtrip_diff(tmp_path, old, new) == new

"""Shared fixtures for the pipeline tests.

The real game data is copyrighted and never committed, so these tests run the
scripts against a tiny synthetic game tree and a stub ``lcf2xml`` that mimics
the real tool's file-naming behaviour (it writes ``<stem>.<other ext>`` into
its working directory). That is enough to exercise the file plumbing:
layering, hardlinks, timestamps, patches and manifests.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

STUB_LCF2XML = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in for lcf2xml: LCF <-> XML by extension, output in cwd."""
    import sys
    from pathlib import Path
    SWAP = {".lmu": ".emu", ".ldb": ".edb", ".lmt": ".emt", ".lsd": ".esd"}
    SWAP.update({v: k for k, v in SWAP.items()})
    args = iter(sys.argv[1:])
    for a in args:
        if a == "--encoding":
            next(args)
            continue
        if a.startswith("--"):
            continue
        src = Path(a)
        out = Path.cwd() / (src.stem + SWAP[src.suffix.lower()])
        # Encode the source so tests can tell what produced the output.
        out.write_bytes(b"converted:" + src.read_bytes())
''')


def load_script(name: str):
    """Import scripts/<name>.py as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lcf2xml(tmp_path: Path) -> Path:
    stub = tmp_path / "bin" / "lcf2xml"
    stub.parent.mkdir(parents=True)
    stub.write_text(STUB_LCF2XML)
    stub.chmod(0o755)
    return stub


def write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)
    return path


@pytest.fixture
def game_src(tmp_path: Path) -> Path:
    """A minimal ``original/`` with a base game and a patch overlay."""
    src = tmp_path / "original"
    base = src / "Game_v1" / "game"
    write(base / "RPG_RT.ini", b"[RPG_RT]\r\nGameTitle=Base\r\n")
    write(base / "RPG_RT.ldb", b"LDB-base")
    write(base / "RPG_RT.lmt", b"LMT-base")
    write(base / "Map0001.lmu", b"MAP1-base")
    write(base / "Map0002.lmu", b"MAP2-base")
    write(base / "Picture" / "pic.png", b"PNG")
    patch = src / "Game_v1_patch1" / "game"
    write(patch / "RPG_RT.ldb", b"LDB-patch")
    write(patch / "Map0002.lmu", b"MAP2-patch")
    return src


def run_script(name: str, *args: str, cwd: Path, check: bool = True,
               **kw) -> subprocess.CompletedProcess:
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py"), *map(str, args)],
        cwd=cwd, capture_output=True, text=True, **kw)
    if check and res.returncode != 0:
        raise AssertionError(
            f"{name}.py {' '.join(map(str, args))} exited {res.returncode}\n"
            f"--- stdout\n{res.stdout}\n--- stderr\n{res.stderr}")
    return res


@pytest.fixture
def extracted(tmp_path: Path, game_src: Path, lcf2xml: Path) -> Path:
    """Run extract.py on the synthetic game; returns the work dir."""
    run_script("extract", "--src", game_src, "--lcf2xml", lcf2xml,
               "--report", "build/extract-report.md", cwd=tmp_path)
    return tmp_path


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def age(path: Path, seconds: int) -> None:
    """Move a file's mtime into the past (avoids sleeping in tests)."""
    st = path.stat()
    os.utime(path, (st.st_atime - seconds, st.st_mtime - seconds))

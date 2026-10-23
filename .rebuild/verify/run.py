"""Verification runner: the original records reference results, the rebuild must produce the same.

    python .rebuild/verify/run.py record [NAME ...]   original -> refs/NAME.json
    python .rebuild/verify/run.py check  [NAME ...]   rebuild  -> compared with refs/NAME.json
    python .rebuild/verify/run.py stable [NAME ...]   original twice: are the results repeatable?
    python .rebuild/verify/run.py list

NAME is a scenario (scenarios/*.py, the module run by the fake addon) or a function check
(functions/*.py, functions of the program called directly). No NAME: all of them.
check also takes NAME:a.b (only that part of the result: a step that completes one behavior).
Run with the Python of the chosen environment (see PLAN.md).
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE / "scenarios"))  # scenarios may share data
from addon import FakeAddon  # noqa: E402
from github import FakeGitHub  # noqa: E402
import versions  # noqa: E402

REFS = HERE / "refs"
FIXTURES = versions.REPO.parent / "fixtures"  # next to the clone; large files never committed


class Context:
    """What a scenario gets: fresh folders, the fake addon and fake GitHub for one version."""

    def __init__(self, version: str, workdir: Path) -> None:
        self.version = version
        self.program = versions.program_dir(version)
        self.workdir = workdir
        self.fixtures = FIXTURES
        self._n = 0

    def folder(self, name: str = "game") -> Path:
        self._n += 1
        path = self.workdir / f"{self._n:02d}-{name}"
        path.mkdir(parents=True)
        return path

    def addon(self, **kw) -> FakeAddon:
        return FakeAddon(self.folder(), self.program, **kw)

    def github(self) -> FakeGitHub:
        return FakeGitHub(self.folder("github"))


def names() -> list[str]:
    return sorted(p.stem for d in ("scenarios", "functions") for p in (HERE / d).glob("[a-z]*.py"))


def produce(name: str, version: str) -> object:
    workdir = Path(tempfile.mkdtemp(prefix=f"lt-{name}-"))
    try:
        scenario = HERE / "scenarios" / f"{name}.py"
        if scenario.is_file():
            spec = importlib.util.spec_from_file_location(name, scenario)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            result = mod.run(Context(version, workdir))
        else:  # function check: a fresh interpreter that imports the program itself
            p = subprocess.run([sys.executable, str(HERE / "functions" / f"{name}.py"),
                                str(versions.program_dir(version)), version, str(workdir)],
                               capture_output=True, text=True, timeout=600,
                               env={"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
                                    "PATH": "/usr/bin:/bin", "HOME": str(Path.home())})
            if p.returncode:
                raise RuntimeError(f"{name} failed:\n{p.stderr}")
            result = json.loads(p.stdout)
        return json.loads(json.dumps(result, ensure_ascii=False))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def pretty(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)


def main() -> int:
    cmd, wanted = sys.argv[1], sys.argv[2:] or names()
    if cmd == "list":
        print("\n".join(names()))
        return 0
    REFS.mkdir(exist_ok=True)
    failed = []
    for name in wanted:
        if cmd == "record":
            (REFS / f"{name}.json").write_text(pretty(produce(name, "original")) + "\n",
                                               encoding="utf-8")
            print(f"recorded {name}")
        elif cmd == "stable":
            a, b = produce(name, "original"), produce(name, "original")
            print(("stable   " if a == b else "UNSTABLE ") + name)
            if a != b:
                failed.append(name)
                diff = difflib.unified_diff(pretty(a).splitlines(), pretty(b).splitlines(),
                                            "run 1", "run 2", lineterm="", n=2)
                print("\n".join(list(diff)[:80]))
        elif cmd == "check":
            base, _, part = name.partition(":")
            ref = json.loads((REFS / f"{base}.json").read_text(encoding="utf-8"))
            got = produce(base, "rebuild")
            for key in filter(None, part.split(".")):
                ref, got = ref[key], got.get(key) if isinstance(got, dict) else None
            if got == ref:
                print(f"ok       {name}")
            else:
                failed.append(name)
                print(f"DIFFERS  {name}")
                diff = difflib.unified_diff(pretty(ref).splitlines(), pretty(got).splitlines(),
                                            "original", "rebuild", lineterm="", n=2)
                print("\n".join(list(diff)[:80]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

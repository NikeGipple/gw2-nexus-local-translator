"""Fake Text Translator: starts the module like the addon does and records everything it does.

The module folder is laid out as in the game (addons/text_translator/modules/<folder>), with the
program files of one version copied into it. The fake addon speaks the modulep v2 protocol, keeps
a cache.db like the addon (every Text received is saved), and captures stderr, the network
requests (fake network, see inject/sitecustomize.py) and the stub translator calls.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

INJECT = Path(__file__).resolve().parent / "inject"
PROGRAM_FILES = ("*.py", "glossary_*.default.json")

CACHE_SCHEMA = """CREATE TABLE IF NOT EXISTS translations (
    id INTEGER NOT NULL, cache_key TEXT NOT NULL, src_hash INTEGER, version INTEGER NOT NULL,
    timestamp INTEGER NOT NULL, text TEXT, PRIMARY KEY (id, cache_key))"""
OLD_CACHE_SCHEMA = """CREATE TABLE IF NOT EXISTS translations (
    id INTEGER NOT NULL, cache_key TEXT NOT NULL, version INTEGER NOT NULL,
    timestamp INTEGER NOT NULL, text TEXT, PRIMARY KEY (id, cache_key))"""


def signed(u64: int) -> int:
    return u64 - (1 << 64) if u64 >= 1 << 63 else u64


def text_message(key: int, src_hash: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return bytes([0]) + struct.pack("<IQH", key, src_hash, len(raw)) + raw


class FakeAddon:
    def __init__(self, workdir: Path, program: Path, *, folder: str = "local_translator_it",
                 args: list[str] | None = None, routes: dict | None = None,
                 stub_ct2: dict | None = None, files: dict[str, bytes | str] | None = None,
                 cache_db: str | None = "new", cache_rows: list[tuple] = (),
                 cache_key: str = "it", python: str = sys.executable) -> None:
        """cache_db: "new" (v0.2.0 layout), "old" (v0.1 layout, no src_hash) or None (no file).
        cache_rows: (id, src_hash signed, text) rows of cache.db under `cache_key`."""
        self.workdir = workdir
        self.module_dir = workdir / "addons" / "text_translator" / "modules" / folder
        self.module_dir.mkdir(parents=True)
        for pattern in PROGRAM_FILES:
            for f in program.glob(pattern):
                shutil.copy2(f, self.module_dir / f.name)
        for rel, content in (files or {}).items():
            path = self.module_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        self.cache_key = cache_key
        self.cache_db = self.module_dir / "cache.db"
        if cache_db is not None:
            con = sqlite3.connect(self.cache_db)
            con.execute(CACHE_SCHEMA if cache_db == "new" else OLD_CACHE_SCHEMA)
            if cache_db == "new":
                con.executemany("INSERT INTO translations VALUES (?, ?, ?, 1, 0, ?)",
                                [(k, cache_key, h, t) for k, h, t in cache_rows])
            con.commit()
            con.close()
        self.args = list(args or [])
        self.routes_path = workdir / "routes.json"
        self.set_routes(routes or {})
        self.net_log = workdir / "net.jsonl"
        self.ct2_log = workdir / "ct2.jsonl"
        self.stub_ct2 = stub_ct2
        self.python = python
        self.proc: subprocess.Popen | None = None
        self.handshake: bytes | None = None
        self.messages: list[tuple[int, int, int, str]] = []  # (kind, key, hash, text)
        self.stderr: list[str] = []
        self.returncode: int | None = None
        self._activity = time.monotonic()
        self._cv = threading.Condition()

    # -- world ----------------------------------------------------------------------------
    def set_routes(self, routes: dict) -> None:
        tmp = self.routes_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"routes": routes}), encoding="utf-8")
        os.replace(tmp, self.routes_path)

    # -- process --------------------------------------------------------------------------
    def start(self, extra_env: dict[str, str] | None = None) -> "FakeAddon":
        env = {k: v for k, v in os.environ.items() if not k.startswith("LT_")}
        env.update(PYTHONPATH=str(INJECT), PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1",
                   LT_NET_ROUTES=str(self.routes_path), LT_NET_LOG=str(self.net_log))
        if self.stub_ct2 is not None:
            stub = self.workdir / "stub_ct2.json"
            stub.write_text(json.dumps(self.stub_ct2), encoding="utf-8")
            env.update(LT_STUB_CT2=str(stub), LT_CT2_LOG=str(self.ct2_log))
        env.update(extra_env or {})
        self.handshake, self.messages, self.stderr, self.returncode = None, [], [], None
        self.proc = subprocess.Popen([self.python, str(self.module_dir / "lt_module.py"), *self.args],
                                     cwd=self.workdir, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def _read_exact(self, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _read_stdout(self) -> None:
        con = None
        while True:
            head = self._read_exact(4)
            if head is None:
                break
            body = self._read_exact(struct.unpack("<I", head)[0])
            if body is None:
                break
            with self._cv:
                self._activity = time.monotonic()
                if self.handshake is None:
                    self.handshake = body
                    self._cv.notify_all()
                    continue
            kind = body[0]
            if kind == 0:
                key, h, n = struct.unpack_from("<IQH", body, 1)
                msg = (0, key, h, body[15:15 + n].decode("utf-8"))
                if self.cache_db.is_file():  # the addon saves every translation it receives
                    con = con or sqlite3.connect(self.cache_db, timeout=30)
                    if "src_hash" in {r[1] for r in con.execute("PRAGMA table_info(translations)")}:
                        con.execute("INSERT OR REPLACE INTO translations VALUES (?, ?, ?, 1, 0, ?)",
                                    (key, self.cache_key, signed(h), msg[3]))
                        con.commit()
            else:
                msg = (kind, struct.unpack_from("<I", body, 1)[0], 0, "")
            with self._cv:
                self.messages.append(msg)
                self._cv.notify_all()
        if con:
            con.close()

    def _read_stderr(self) -> None:
        for raw in self.proc.stderr:
            with self._cv:
                self._activity = time.monotonic()
                self.stderr.append(raw.decode("utf-8", "replace").rstrip("\n"))
                self._cv.notify_all()

    def send(self, key: int, src_hash: int, text: str) -> None:
        self.send_raw(text_message(key, src_hash, text))

    def send_raw(self, body: bytes) -> None:
        self.send_many([body])

    def send_many(self, bodies: list[bytes]) -> None:
        """Several messages in a single write."""
        self.proc.stdin.write(b"".join(struct.pack("<I", len(b)) + b for b in bodies))
        self.proc.stdin.flush()

    # -- waiting --------------------------------------------------------------------------
    def wait_for(self, predicate, timeout: float = 30) -> bool:
        end = time.monotonic() + timeout
        with self._cv:
            while not predicate():
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(min(left, 0.2))
        return True

    def wait_handshake(self, timeout: float = 30) -> bytes | None:
        self.wait_for(lambda: self.handshake is not None or self.proc.poll() is not None, timeout)
        return self.handshake

    def wait_answers(self, keys, timeout: float = 60) -> bool:
        keys = set(keys)
        return self.wait_for(lambda: keys <= {m[1] for m in self.messages}, timeout)

    def wait_log(self, pattern: str, timeout: float = 30) -> bool:
        rx = re.compile(pattern)
        return self.wait_for(lambda: any(rx.search(line) for line in self.stderr), timeout)

    def settle(self, quiet: float = 1.0, timeout: float = 60) -> None:
        """Wait until the module has written nothing for `quiet` seconds."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._cv:
                idle = time.monotonic() - self._activity
            if idle >= quiet:
                return
            time.sleep(min(quiet - idle, 0.1) + 0.01)

    def close(self, timeout: float = 30) -> int:
        """End of the game: the addon closes stdin; the module exits by itself."""
        self.proc.stdin.close()
        try:
            self.returncode = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:  # a rebuild still in progress may not exit yet
            self.proc.kill()
            self.proc.wait(10)
            self.returncode = "did not exit"
        self.wait_for(lambda: False, 0.3)  # let the reader threads drain
        return self.returncode

    def kill(self) -> int:
        """The addon ends the module without closing the connection."""
        self.proc.kill()
        self.returncode = self.proc.wait(10)
        return self.returncode

    def run_command(self, timeout: float = 120) -> dict:
        """A developer command (exits by itself, no protocol): stdout, stderr, exit code."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("LT_")}
        env.update(PYTHONPATH=str(INJECT), PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1",
                   LT_NET_ROUTES=str(self.routes_path), LT_NET_LOG=str(self.net_log))
        p = subprocess.run([self.python, str(self.module_dir / "lt_module.py"), *self.args],
                           cwd=self.workdir, env=env, capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        err = self.norm(p.stderr.decode("utf-8", "replace"))
        if "Traceback (most recent call last)" in err:  # only the exception: lines differ by version
            err = "Traceback ... " + err.strip().splitlines()[-1]
        return {"exit": p.returncode, "stdout": self.norm(p.stdout.decode("utf-8", "replace")),
                "stderr": err}

    # -- results --------------------------------------------------------------------------
    def norm(self, text: str) -> str:
        text = text.replace(str(self.workdir), "<W>")
        text = text.split("\0Traceback", 1)[0]  # tracebacks name lines of one version only
        text = re.sub(r"\d+\.\d+ s\b", "<t> s", text)
        return text

    def log_lines(self, drop: tuple[str, ...] = ("i: stats ",)) -> list[str]:
        """stderr lines, normalized, sorted (threads write in any order)."""
        return sorted(self.norm(line) for line in self.stderr if not line.startswith(drop))

    def file_log(self, drop: tuple[str, ...] = ("INFO stats ",)) -> dict[str, list[str]]:
        """The module's log files (local_translator*.log), timestamps removed, normalized, sorted."""
        return {p.name: self._log_file(p.read_text(encoding="utf-8"), drop)
                for p in sorted(self.module_dir.glob("local_translator*.log*"))}

    def _log_file(self, text: str, drop: tuple[str, ...]) -> list[str]:
        lines = []
        for line in text.splitlines():
            m = re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} (.*)", line)
            if m:
                lines.append(m.group(1))
            elif lines:  # traceback lines: kept out (see norm)
                continue
        return sorted(self.norm(x) for x in lines if not x.startswith(drop))

    def answers(self) -> dict[str, list[list]]:
        """key -> [(kind, hash, text), ...] in arrival order (order across keys is not kept)."""
        out: dict[str, list[list]] = {}
        for kind, key, h, text in self.messages:
            out.setdefault(str(key), []).append([kind, h, text])
        return dict(sorted(out.items(), key=lambda kv: int(kv[0])))

    def net_requests(self) -> list[dict]:
        if not self.net_log.is_file():
            return []
        rows = [json.loads(line) for line in self.net_log.read_text(encoding="utf-8").splitlines()]
        return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))

    def ct2_calls(self) -> list[dict]:
        if not self.ct2_log.is_file():
            return []
        return [json.loads(line) for line in self.ct2_log.read_text(encoding="utf-8").splitlines()]

    def file_text(self, rel: str) -> str | None:
        path = self.module_dir / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def jsonl(self, rel: str) -> list | None:
        text = self.file_text(rel)
        return None if text is None else [json.loads(x) for x in text.splitlines() if x.strip()]

    def files(self, rel: str = "") -> list[str]:
        root = self.module_dir / rel
        return sorted(str(p.relative_to(self.module_dir)).replace(os.sep, "/")
                      for p in root.rglob("*") if p.is_file()
                      and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts)

    def db(self, rel: str, query: str, params: tuple = ()) -> list[list] | None:
        path = self.module_dir / rel
        if not path.is_file():
            return None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return sorted([list(r) for r in con.execute(query, params)], key=repr)
        finally:
            con.close()

    def tables(self, rel: str) -> dict[str, list[str]] | None:
        """Schema of a database: table -> column names (as declared order)."""
        path = self.module_dir / rel
        if not path.is_file():
            return None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
            return {n: [c[1] for c in con.execute(f"PRAGMA table_info({n})")] for n in names}
        finally:
            con.close()

    def cache_rows(self) -> list[list] | None:
        return self.db("cache.db", "SELECT id, cache_key, src_hash, text FROM translations")

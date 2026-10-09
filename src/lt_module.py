"""Local Translator: local translation module for Ideka's "Text Translator" Nexus addon.

Local Translator is meant for any language the game can be translated into from English; each
language is built as its own module (local_translator_<lang>). Italian is the first one, and for
now the language is set in the code (CACHE_KEY, IT\\ folder, *_it files, the GitHub URLs, and the
plural rules lt_server.PLURAL_RULES from plurale_it.py).

Protocol: https://github.com/ideka/modulep (version 1). The addon starts this exe itself
(module.toml), sends every game text with its string ID on stdin and reads the translations
on stdout; stderr goes to the Nexus log. The addon keeps the translations in cache.db next to
this exe (table translations: id, cache_key, version, timestamp, text).

Glossary, patch, OPUS-MT engine and map come from lt_server.py, imported as a library.
Data lives in IT\\ next to this exe (model, glossary, patch, cache_it.jsonl, map_it.db).

Developer commands (strumenti.bat options 4 and 5): --export-review and --import-review.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import sqlite3
import struct
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# PyInstaller bundles lt_server.py too (--paths).
sys.path.insert(0, str(Path(__file__).resolve().parent))  # lt_server.py sits next to this file
import lt_server as lt  # noqa: E402

PROTOCOL = 1
SOURCE_LANG = 0          # English: the game must be set to English
RESULT_VERSION = 1       # raise it to make the addon ask again for every cached text
CACHE_KEY = "it"
MAX_MESSAGE = 0x10_0000
KIND_TEXT, KIND_CANCEL = 0, 1

STATS_EVERY = 300  # default seconds between two "stats" lines in the log (--stats-every)

TRIVIAL_RE = re.compile(r"\(\(\d+\)\)|\(new string\)")

log = logging.getLogger("lt")


# --------------------------------------------------------------------------- #
# Streams and protocol
# --------------------------------------------------------------------------- #
def binary_streams():
    """Real stdin/stdout/stderr as binary files. A --noconsole exe may get sys.stdin = None
    even when the addon passes pipes, so on Windows the handles are taken from the OS."""
    if os.name != "nt":
        return sys.stdin.buffer, sys.stdout.buffer, sys.stderr.buffer
    import ctypes
    import msvcrt
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = ctypes.c_void_p
    out = []
    for num, mode in ((-10, "rb"), (-11, "wb"), (-12, "wb")):
        h = k32.GetStdHandle(ctypes.c_uint32(num & 0xFFFFFFFF))
        if not h or h == ctypes.c_void_p(-1).value:
            out.append(None)
            continue
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY if mode == "rb" else 0)
        msvcrt.setmode(fd, os.O_BINARY)
        out.append(os.fdopen(fd, mode, buffering=0))
    return tuple(out)


def put_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 0xFFFF:
        raise ValueError("string longer than 65535 bytes")
    return struct.pack("<H", len(raw)) + raw


class Channel:
    def __init__(self, rx, tx) -> None:
        self.rx, self.tx = rx, tx
        self.lock = threading.Lock()

    def _read_exact(self, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = self.rx.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def read(self) -> bytes | None:
        head = self._read_exact(4)
        if head is None:
            return None
        (size,) = struct.unpack("<I", head)
        if size > MAX_MESSAGE:
            raise ValueError(f"message too big ({size} bytes)")
        return self._read_exact(size)

    def send(self, contents: list[bytes]) -> None:
        data = b"".join(struct.pack("<I", len(c)) + c for c in contents)
        with self.lock:
            self.tx.write(data)
            self.tx.flush()

    def handshake(self) -> None:
        self.send([struct.pack("<IiI", PROTOCOL, SOURCE_LANG, RESULT_VERSION) + put_string(CACHE_KEY)])

    @staticmethod
    def text(key: int, text: str) -> bytes:
        return bytes([KIND_TEXT]) + struct.pack("<I", key) + put_string(text)

    @staticmethod
    def cancel(key: int) -> bytes:
        return bytes([KIND_CANCEL]) + struct.pack("<I", key)


BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def lower_priority() -> None:
    """Run below normal priority (model threads included), so the game always gets the CPU first."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        k32.SetPriorityClass.restype = ctypes.c_int
        if k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
            log.info("process priority: below normal")
        else:
            log.warning("cannot lower the process priority (Windows error %d)", ctypes.GetLastError())
    except Exception as exc:  # noqa: BLE001
        log.warning("cannot lower the process priority: %s", exc)


def model_threads(requested: int | None = None) -> int:
    """CPU threads for the model: --threads if given (module.toml), otherwise a quarter of the
    logical CPUs, at most 2 (the game needs the rest)."""
    cpus = os.cpu_count() or 2
    if requested and requested > 0:
        return min(requested, cpus)
    return max(1, min(2, cpus // 4))


class StderrHandler(logging.Handler):
    """Nexus log lines: 'e: ', 'w: ', 'i: ', 'd: ' prefixes, '\\0' instead of newlines."""

    LETTERS = {logging.ERROR: "e", logging.WARNING: "w", logging.INFO: "i", logging.DEBUG: "d"}

    def __init__(self, stream) -> None:
        super().__init__()
        self.stream = stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            letter = self.LETTERS.get(record.levelno, "e" if record.levelno > logging.ERROR else "i")
            msg = self.format(record).replace("\r", "").replace("\n", "\0")
            self.stream.write(f"{letter}: {msg}\n".encode("utf-8", "replace"))
            self.stream.flush()
        except Exception:  # noqa: BLE001 - never let logging kill the module
            pass


# --------------------------------------------------------------------------- #
# Patch and glossary cleanup on the addon's cache.db
# --------------------------------------------------------------------------- #
def has_table(db: Path) -> bool:
    if not db or not db.is_file():
        return False
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='translations'"
                           ).fetchone() is not None
    finally:
        con.close()


class CachePatch(lt.Patch):
    """Same rules as lt_server.Patch, applied to Text Translator's cache.db.

    The whole patch is written into cache.db in advance (since 2026-10-07): the addon reads
    cache.db when the game starts, so from the next start every patch text is Italian the first
    time it appears, also with "Pause Refreshes" on (no refresh needed). Priority: reviewed >
    automatic > local server; automatic rows purged by a glossary change (skip file) are not
    written. Texts that left the patch ('drop' and rows written earlier by us) are removed.

    If the string IDs check fails (IdCheck), the patch rows are removed from cache.db and the
    patch stays off (marker file patch_it.ids_failed.json) until a later check passes.
    """

    def apply(self, db: Path | None) -> int:
        with self.lock:
            reviewed = list(self.strings.items())
            auto = list(self.auto.items())
            drop = set(self.drop)
        if not (reviewed or auto or drop) or not has_table(db):
            return 0
        if self.ids_failed():
            log.warning("patch not applied: the string IDs did not match the map (%s)",
                        self._ids_failed_path().name)
            return 0
        con = sqlite3.connect(str(db), timeout=10)
        try:
            current = dict(con.execute("SELECT id, text FROM translations WHERE cache_key = ?",
                                       (CACHE_KEY,)))
            skipped = self._skipped() if auto else set()
            applied = self._read_applied()
            write = [(k, v) for k, v in reviewed if current.get(k) != v]
            for k, v in auto:
                if current.get(k) == v or k in skipped:
                    continue
                write.append((k, v))  # new, or a valid automatic text beats the local server's one
            published = {k for k, _ in reviewed} | {k for k, _ in auto}
            remove = {k for k, t in applied.items()
                      if k not in published and current.get(k) == t} if auto else set()
            dropped_before = self._read_dropped()
            remove |= {k for k in drop - dropped_before
                       if k not in published and current.get(k) is not None}
            now = int(time.time())
            with con:
                con.executemany(
                    "INSERT INTO translations (id, cache_key, version, timestamp, text) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(cache_key, id) DO UPDATE SET "
                    "text = excluded.text, version = excluded.version, timestamp = excluded.timestamp",
                    [(k, CACHE_KEY, RESULT_VERSION, now, v) for k, v in write])
                con.executemany("DELETE FROM translations WHERE cache_key = ? AND id = ?",
                                [(CACHE_KEY, k) for k in remove])
        finally:
            con.close()
        if self.auto_current() and self.skip_path and self.skip_path.exists():
            try:  # patch made with the player's glossary: the old blocks are no longer needed
                self.skip_path.unlink()
            except OSError as exc:
                log.warning("cannot remove %s: %s", self.skip_path, exc)
        for k in remove:
            current.pop(k, None)
        dpath = self._dropped_path()
        if dpath and drop - dropped_before:
            try:
                dpath.write_text(json.dumps(sorted(drop)), encoding="utf-8")
            except OSError as exc:
                log.warning("cannot save %s: %s", dpath, exc)
        self.remember_applied(current, write)
        if write:
            log.info("patch v%d: %d translations updated in cache.db (visible at next game start)",
                     self.version, len(write))
        if remove:
            log.info("patch v%d: %d old translations removed from cache.db", self.version, len(remove))
        return len(write) + len(remove)

    def remember_applied(self, current: dict[int, str], write: list[tuple[int, str]]) -> None:
        """Registry of the automatic texts now held by the addon, to update them in a new version."""
        path = self._applied_path()
        if not path:
            return
        with self.lock:
            auto = dict(self.auto)
        final = dict(current)
        final.update(write)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({str(k): v for k, v in auto.items() if final.get(k) == v},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("cannot save %s: %s", path, exc)

    # -- string IDs check failed: patch off -----------------------------------------------
    def _ids_failed_path(self) -> Path | None:
        return self.skip_path.with_name("patch_it.ids_failed.json") if self.skip_path else None

    def ids_failed(self) -> bool:
        path = self._ids_failed_path()
        return bool(path and path.exists())

    def withdraw(self, db: Path | None, reason: str) -> int:
        """The string IDs do not match: remove from cache.db every row that holds a patch text
        (the addon asks for them again) and keep the patch off until a check passes."""
        path = self._ids_failed_path()
        if path:
            try:
                path.write_text(json.dumps({"patch_version": self.version, "reason": reason,
                                            "time": int(time.time())}), encoding="utf-8")
            except OSError as exc:
                log.warning("cannot save %s: %s", path, exc)
        if not has_table(db):
            return 0
        with self.lock:
            texts = dict(self.auto)
            texts.update(self.strings)
        con = sqlite3.connect(str(db), timeout=10)
        try:
            current = dict(con.execute("SELECT id, text FROM translations WHERE cache_key = ?",
                                       (CACHE_KEY,)))
            remove = [k for k, t in current.items() if texts.get(k) == t]
            with con:
                con.executemany("DELETE FROM translations WHERE cache_key = ? AND id = ?",
                                [(CACHE_KEY, k) for k in remove])
        finally:
            con.close()
        applied = self._applied_path()
        if applied and applied.exists():
            try:
                applied.unlink()
            except OSError as exc:
                log.warning("cannot remove %s: %s", applied, exc)
        log.error("patch: %d patch texts removed from cache.db (string IDs do not match)", len(remove))
        return len(remove)

    def ids_ok(self, db: Path | None) -> None:
        """A check passed: if the patch was off because of an earlier failed check, turn it on."""
        path = self._ids_failed_path()
        if path and path.exists():
            try:
                path.unlink()
            except OSError as exc:
                log.warning("cannot remove %s: %s", path, exc)
                return
            log.info("string IDs match again: patch turned back on")
            self.apply(db)

    def answer(self, key: int, skipped: set[int]) -> str | None:
        with self.lock:
            if key in self.strings:
                return self.strings[key]
            if key in self.auto and key not in skipped:
                return self.auto[key]
        return None


class ModuleEngine(lt.Engine):
    """lt_server.Engine whose glossary cleanup works on cache.db (rows found by their translated text)."""

    def _purge_addon_db(self, texts: set[str]) -> None:
        db = self.addon_db
        texts = {t for t in texts if len(t.strip()) >= 3}
        if not texts or not has_table(db):
            return
        keep = self.patch.keys() if self.patch else set()
        try:
            con = sqlite3.connect(str(db), timeout=10)
            try:
                with con:
                    doomed: set[int] = set()
                    for t in texts:
                        doomed.update(k for (k,) in con.execute(
                            "SELECT id FROM translations WHERE cache_key = ? AND instr(text, ?) > 0",
                            (CACHE_KEY, t)))
                    doomed -= keep
                    if self.patch:
                        self.patch.suppress(doomed)
                    con.executemany("DELETE FROM translations WHERE cache_key = ? AND id = ?",
                                    [(CACHE_KEY, k) for k in doomed])
            finally:
                con.close()
            log.info("cache.db: %d old translations removed (glossary changed; "
                     "asked again at next game start)", len(doomed))
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot clean %s: %s", db, exc)


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class IdCheck:
    """Checks that the string IDs Text Translator sends are the ones the patch was built with,
    by comparing the English text it sends with the map (e.g. after an update of the addon).
    If too many differ, the patch is no longer used (it would show the wrong texts)."""

    def __init__(self, keymap: lt.KeyMap | None, on_fail=None, on_pass=None) -> None:
        self.keymap = keymap
        self.on_fail, self.on_pass = on_fail, on_pass  # run in a thread (they use cache.db)
        self.match = self.mismatch = 0
        self.failed = False
        self.reported = False
        self.known: dict[int, str] = {}
        if keymap:
            with keymap.lock:
                self.known = dict(keymap.con.execute(
                    "SELECT key, en FROM texts WHERE how NOT LIKE 'contrib%'"))

    def see(self, key: int, en: str) -> None:
        if self.reported or key not in self.known:
            return
        if lt.norm(self.known[key]) == lt.norm(en):
            self.match += 1
        else:
            self.mismatch += 1
        if self.match + self.mismatch >= 50:
            self.reported = True
            if self.mismatch > self.match:
                self.failed = True
                log.error("string IDs do not match the map (%d equal, %d different): "
                          "patch DISABLED", self.match, self.mismatch)
                if self.on_fail:
                    threading.Thread(target=self.on_fail, args=(f"{self.match} equal, "
                                     f"{self.mismatch} different",), daemon=True,
                                     name="patch-withdraw").start()
            else:
                log.info("string IDs check: %d equal, %d different -> same IDs as the map",
                         self.match, self.mismatch)
                if self.on_pass:
                    threading.Thread(target=self.on_pass, daemon=True, name="patch-ids-ok").start()


class Stats:
    """Counters for the periodic "stats" log line, to see where time goes while playing:

    requests  texts received, by how they were answered: patch, cache (already translated, glossary
              or nothing to translate), model (had to wait for the translation), empty (no text)
    wait      seconds from arrival to answer of the "model" requests: average, 95th percentile, max
    queue     longest queue of lines waiting for the model
    model     lines translated, how long the worker was busy (glossary protection + model)
    cpu       CPU used by the whole module (all threads), in % of one core: 100% = one core busy
    The line is written only if something happened (requests, translations or CPU >= 1%).
    """

    def __init__(self, engine: ModuleEngine, every: float = STATS_EVERY) -> None:
        self.engine = engine
        self.every = every
        self.since = time.monotonic()
        self.cpu_since = time.process_time()
        self.base = self._totals()
        self.counts = {"patch": 0, "cache": 0, "model": 0, "empty": 0}
        self.waits: list[float] = []
        self.queue_max = 0
        self.sends = 0  # groups of answers sent to the addon (each one can cause an addon refresh)

    def _totals(self) -> tuple[int, float, float, int]:
        e = self.engine
        return e.done_count, e.time_prepare, e.time_model, len(e.given_up)

    def due(self) -> bool:
        return time.monotonic() - self.since >= self.every

    def report(self, waiting: dict[int, float]) -> str | None:
        """Text of the stats line for the period just ended (None if nothing happened), then reset."""
        now = time.monotonic()
        cpu_now = time.process_time()
        elapsed = max(now - self.since, 1e-6)
        cpu = (cpu_now - self.cpu_since) * 100 / elapsed  # % of one core
        period = f"{elapsed / 60:.0f} min" if elapsed >= 90 else f"{elapsed:.0f} s"
        done, prep, model, gave_up = (a - b for a, b in zip(self._totals(), self.base))
        total = sum(self.counts.values())
        text = None
        if total or done or cpu >= 1:
            parts = ", ".join(f"{k} {v * 100 / total:.0f}%" for k, v in self.counts.items()) if total else ""
            text = (f"stats {period}: cpu {cpu:.0f}% of one core ({os.cpu_count() or '?'} cores); "
                    f"{total} requests" + (f" ({parts})" if parts else ""))
            if self.waits:
                w = sorted(self.waits)
                p95 = w[min(len(w) - 1, int(len(w) * 0.95))]
                text += (f"; model wait avg {sum(w) / len(w):.1f} s, p95 {p95:.1f} s, "
                         f"max {w[-1]:.1f} s")
            if waiting:
                text += (f"; still waiting {len(waiting)} "
                         f"(oldest {now - min(waiting.values()):.0f} s)")
            text += f"; queue max {self.queue_max} lines; sent {self.sends} groups"
            busy = prep + model
            speed = f"{done / busy:.1f} lines/s, " if busy > 0 else ""
            text += (f"; translated {done} lines in {busy:.1f} s ({speed}glossary {prep:.1f} s, "
                     f"model {model:.1f} s)")
            if gave_up:
                text += f"; given up {gave_up} lines"
        self.since, self.cpu_since, self.base = now, cpu_now, self._totals()
        self.counts = dict.fromkeys(self.counts, 0)
        self.waits = []
        self.queue_max = 0
        self.sends = 0
        return text


class Dispatcher:
    """Receives texts, answers at once when possible (patch, glossary, cache) and otherwise
    waits for the translation worker. Each answer is also saved in the map (key, en, it).

    Requests waiting for the model are indexed by line, so when a batch is finished only the
    requests waiting for those lines are checked (before, every waiting request was checked
    again at every wake-up, holding the lock the worker needs: 66 ms with 5,000 requests)."""

    def __init__(self, engine: ModuleEngine, patch: CachePatch, keymap: lt.KeyMap | None,
                 channel: Channel, stats_every: float = STATS_EVERY,
                 cache_db: Path | None = None) -> None:
        self.engine, self.patch, self.keymap, self.channel = engine, patch, keymap, channel
        self.inbox: queue.SimpleQueue[tuple[int, str]] = queue.SimpleQueue()
        self.pending: dict[int, str] = {}        # key -> English text, waiting for the model
        self.missing: dict[int, set[str]] = {}   # key -> lines not translated yet
        self.waiting: dict[str, set[int]] = {}   # line -> keys waiting for it
        self.ids = IdCheck(keymap, on_fail=lambda why: patch.withdraw(cache_db, why),
                           on_pass=lambda: patch.ids_ok(cache_db))
        self.skipped: set[int] = set()
        self.skipped_version: tuple = ()
        self.answered = 0
        self.arrived: dict[int, float] = {}      # key -> when it started waiting for the model
        self.stats = Stats(engine, stats_every)
        with engine.cv:
            engine.finished = []  # the worker now lists the lines of every finished batch

    def submit(self, key: int, text: str) -> None:
        self.inbox.put((key, text))
        with self.engine.cv:
            self.engine.cv.notify_all()

    def _result(self, line: str) -> str:
        fixed = self.engine.glossary.lookup(line)
        return fixed if fixed is not None else self.engine.cache.get(line, line)

    def _ready(self, line: str) -> bool:
        e = self.engine
        return (line in e.cache or line in e.given_up or e.glossary.lookup(line) is not None
                or not lt.Engine._needs_translation(line))

    def _enqueue(self, line: str) -> None:
        e = self.engine
        if line not in e.queued:
            e.queued.add(line)
            e.queue.append(line)

    def _try(self, key: int, text: str, out: list[tuple[int, str, str]]) -> bool:
        """Answer the key if all its lines are ready; otherwise register the missing lines and
        queue them. Called with engine.cv held. Returns True if lines were queued."""
        lines = text.split("\n")
        missing = {line for line in lines if not self._ready(line)}
        if not missing:
            self.pending.pop(key, None)
            self.missing.pop(key, None)
            started = self.arrived.pop(key, None)
            if started is None:
                self.stats.counts["cache"] += 1
            else:
                self.stats.counts["model"] += 1
                self.stats.waits.append(time.monotonic() - started)
            out.append((key, text, "\n".join(self._result(line) for line in lines)))
            return False
        self.pending[key] = text
        self.missing[key] = missing
        self.arrived.setdefault(key, time.monotonic())
        for line in missing:
            self.waiting.setdefault(line, set()).add(key)
            self._enqueue(line)
        return True

    def _forget(self, key: int) -> None:
        """The addon sent a key that is already waiting: drop the old registration."""
        self.arrived.pop(key, None)
        if self.pending.pop(key, None) is None:
            return
        for line in self.missing.pop(key, ()):
            keys = self.waiting.get(line)
            if keys is not None:
                keys.discard(key)
                if not keys:
                    del self.waiting[line]

    def run(self) -> None:
        e = self.engine
        while True:
            out: list[tuple[int, str, str]] = []
            with e.cv:
                if self.inbox.empty() and not e.finished:
                    e.cv.wait(0.5)
                state = self.patch.skipped_state()  # patch or glossary changed: check again
                if state != self.skipped_version:
                    self.skipped, self.skipped_version = self.patch._skipped(), state
                queued = False
                while True:  # new requests
                    try:
                        key, text = self.inbox.get_nowait()
                    except queue.Empty:
                        break
                    self._forget(key)
                    self.ids.see(key, text)
                    if not text.strip() or TRIVIAL_RE.fullmatch(text.strip()):
                        self.stats.counts["empty"] += 1
                        out.append((key, text, text))
                        continue
                    fixed = (None if self.ids.failed or self.patch.ids_failed()
                             else self.patch.answer(key, self.skipped))
                    if fixed is not None:
                        self.stats.counts["patch"] += 1
                        out.append((key, text, fixed))
                        continue
                    queued = self._try(key, text, out) or queued
                done, e.finished = e.finished, []
                for line in done:  # finished batches: only the keys waiting for those lines
                    for key in self.waiting.pop(line, ()):
                        left = self.missing.get(key)
                        if left is None:
                            continue
                        left.discard(line)
                        if not left:  # check all its lines again (one may have left the cache)
                            queued = self._try(key, self.pending[key], out) or queued
                if queued:
                    e.cv.notify_all()
                self.stats.queue_max = max(self.stats.queue_max, len(e.queue))
                report = self.stats.report(self.arrived) if self.stats.due() else None
            if report:
                log.info("%s", report)
            if out:
                self._send(out)

    def _send(self, out: list[tuple[int, str, str]]) -> None:
        msgs = []
        for key, en, it in out:
            try:
                msgs.append(Channel.text(key, it))
            except ValueError:  # translation longer than the game allows: keep the original
                msgs.append(Channel.text(key, en))
        self.channel.send(msgs)
        self.stats.sends += 1
        before = self.answered
        self.answered += len(out)
        if before // 1000 != self.answered // 1000:
            log.info("%d texts answered, %d waiting for the model, %d lines in queue",
                     self.answered, len(self.pending), len(self.engine.queue))
        if self.keymap and not self.ids.failed:
            try:
                self.keymap.save([(k, en, it, "live") for k, en, it in out
                                   if en.strip() and en != it])
            except Exception as exc:  # noqa: BLE001
                log.debug("map save failed: %s", exc)


def cache_db_watcher(patch: CachePatch, cache_db: Path) -> None:
    """Apply the patch to cache.db as soon as the addon has created it (once per start)."""
    for _ in range(720):  # up to one hour
        try:
            if has_table(cache_db):
                patch.apply(cache_db)
                return
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot apply the patch to %s: %s", cache_db, exc)
            return
        time.sleep(5)


# --------------------------------------------------------------------------- #
def main() -> int:
    module_dir = lt.app_dir()  # ...\addons\text_translator\modules\<folder>
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="answer '[IT] text', no model (test)")
    ap.add_argument("--lang-dir", type=Path, default=module_dir / "IT")
    ap.add_argument("--cache-db", type=Path, default=module_dir / "cache.db")
    ap.add_argument("--patch-file", type=Path, default=None, help="local patch (developers)")
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--threads", type=int, default=None,
                    help="CPU threads for the model (default: a quarter of the CPUs, at most 2)")
    ap.add_argument("--stats-every", type=int, default=STATS_EVERY, metavar="SECONDS",
                    help=f"seconds between two 'stats' lines in the log (default {STATS_EVERY}, min 10)")
    ap.add_argument("--export-review", type=Path, metavar="CSV",
                    help="developers: write the ID/English/translation map of this module to a CSV, then exit")
    ap.add_argument("--import-review", type=Path, metavar="CSV",
                    help="developers: merge the 'nuova_traduzione' column of a reviewed CSV into "
                         "--patch-file, then exit")
    # Unknown arguments (e.g. a newer module.toml with an older exe) must not stop the module:
    # argparse would exit before the log exists, and the game would stay untranslated.
    args, unknown = ap.parse_known_args()

    if args.export_review:  # developer commands, not started by the addon
        n = lt.export_review(args.lang_dir / "map_it.db", args.patch_file or args.lang_dir / "patch_it.json",
                             args.export_review)
        print(f"{n} rows written to {args.export_review}")
        return 0
    if args.import_review:
        if not args.patch_file:
            print("--import-review needs --patch-file (the repository's patch\\patch_it.json)")
            return 1
        changed, removed = lt.import_review(args.import_review, args.patch_file)
        print(f"patch {args.patch_file}: {changed} added/changed, {removed} removed")
        return 0

    rx, tx, err = binary_streams()
    if rx is None or tx is None:
        return 2
    channel = Channel(rx, tx)
    channel.handshake()  # first thing: the game waits for it
    sys.stdout = open(os.devnull, "w")  # nothing else may ever reach the real stdout

    args.lang_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(
        module_dir / "local_translator_it.log", maxBytes=500_000, backupCount=1, encoding="utf-8")]
    handlers[0].setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    if err is not None:
        h = StderrHandler(err)
        h.setLevel(logging.INFO)
        h.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(h)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, handlers=handlers)
    log.info("Local Translator IT module started (protocol %d, result version %d)", PROTOCOL, RESULT_VERSION)
    lower_priority()
    if unknown:
        log.warning("ignored unknown arguments: %s", " ".join(unknown))

    # Requests are read and buffered from the very start, while the rest gets ready.
    early: list[tuple[int, str]] = []
    holder: dict[str, Dispatcher] = {}
    maps: list[lt.KeyMap] = []  # closed by the reader on exit
    ready = threading.Event()

    def reader() -> None:
        try:
            while True:
                msg = channel.read()
                if msg is None:
                    break
                if len(msg) < 7 or msg[0] != KIND_TEXT:
                    continue  # unknown message kinds are ignored, as the protocol requires
                key, = struct.unpack_from("<I", msg, 1)
                n, = struct.unpack_from("<H", msg, 5)
                text = msg[7:7 + n].decode("utf-8", "replace")
                if ready.is_set():
                    holder["d"].submit(key, text)
                else:
                    early.append((key, text))
        except Exception:  # noqa: BLE001
            log.exception("stdin reader failed")
        log.info("addon closed the connection: exiting")
        for m in maps:  # merge map_it.db-wal into map_it.db before exiting
            try:
                m.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("map not closed cleanly: %s", exc)
        logging.shutdown()
        os._exit(0)

    threading.Thread(target=reader, daemon=True, name="stdin-reader").start()

    glossary = lt.Glossary()
    if not glossary.load_file(args.lang_dir / "glossary_it.json"):
        glossary.load_file(lt.bundled_dir() / "glossary_it.default.json")
    fake = args.fake or (args.lang_dir / "fake.txt").is_file()
    patch = CachePatch(skip_path=args.lang_dir / "patch_it.skip.json")
    patch.load_file(args.patch_file or args.lang_dir / "patch_it.json")
    patch.glossary = glossary  # to know whether 'auto' was made with this glossary
    engine = ModuleEngine(lt.FakeTranslator() if fake else None, glossary,
                          args.lang_dir / "cache_it.jsonl", args.cache_db, patch=patch)
    keymap = None
    try:
        keymap = lt.KeyMap(args.lang_dir / "map_it.db")
        maps.append(keymap)
        log.info("map: %d strings known", keymap.count())
    except Exception as exc:  # noqa: BLE001
        log.warning("map disabled: %s", exc)

    dispatcher = Dispatcher(engine, patch, keymap, channel, max(10, args.stats_every),
                            args.cache_db)
    holder["d"] = dispatcher
    ready.set()
    for key, text in early:
        dispatcher.submit(key, text)
    early.clear()
    threading.Thread(target=dispatcher.run, daemon=True, name="dispatcher").start()

    if fake:
        log.info("TEST MODE: answering '[IT] text', no real translation")
    else:
        def load_model() -> None:
            model = args.lang_dir / "model"
            try:
                lt.ensure_model(model, args.lang_dir, lt.MODEL_URL)
                threads = model_threads(args.threads)
                translator = lt.CT2Translator(model, threads=threads)
                with engine.cv:
                    engine.translator = translator
                    engine.cv.notify_all()  # wake the worker now, not at its next check
                log.info("model loaded (%d threads)", threads)
            except Exception:  # noqa: BLE001
                log.exception("could not prepare the translation model in %s", model)
        threading.Thread(target=load_model, daemon=True, name="model-loader").start()

    threading.Thread(target=cache_db_watcher, args=(patch, args.cache_db), daemon=True,
                     name="cache-db").start()
    if not args.no_update:
        def glossary_changed() -> None:
            engine.sync_glossary()
            # automatic rows blocked until now may be valid with the new glossary
            try:
                if has_table(args.cache_db):
                    patch.apply(args.cache_db)
            except Exception as exc:  # noqa: BLE001
                log.warning("cannot apply the patch to %s: %s", args.cache_db, exc)

        lt.GlossaryUpdater(glossary, args.lang_dir, glossary_changed, lt.GLOSSARY_URL).start()
        if not args.patch_file:
            lt.PatchUpdater(patch, args.lang_dir, args.cache_db, lt.PATCH_URL).start()

    while True:  # everything runs in threads; the reader exits the process when stdin closes
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())

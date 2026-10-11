"""Local Translator: the curated patch kept by the module in a SQLite archive (IT\\patch_it.db),
written into Text Translator's cache.db and updated from GitHub one piece at a time.

Up to v0.4 the module kept the whole patch in Python dictionaries (patch_it.json, ~15 MB of RAM for
60k entries) and every start compared all of cache.db with all of the patch in Python, rewriting
patch_it.applied.json (as big as "auto"). Now:
- the patch lives in patch_it.db: an answer is a lookup by key, nothing is loaded in memory;
- cache.db is compared with the patch in SQL (ATTACH), only reading it: the rows the addon holds
  with another text are sent to the addon as translations it did not ask for (v0.8), so the game
  shows them at once and the addon saves them in cache.db itself; texts the addon does not hold
  yet are answered from the patch when it asks for them (lookup);
- a new version downloads only the pieces whose sha256 changed (lt_server.split_patch) and
  checks only their keys against cache.db;
- the local registries (applied, skip, dropped, ids_failed) are tables instead of JSON files;
- every entry may carry src_hash, the hash of the English text it was translated from, the same
  Text Translator (v0.2.0) sends with every text and keeps in cache.db (column src_hash): see below.

Text Translator shows a row of cache.db whose src_hash does not match the text the game shows, but
asks the module for it again. So:
- the patch is sent with its src_hash: the addon keeps it as it is, and asks for it by itself if
  ArenaNet changes the English text;
- asked for a key whose src_hash differs from the one received, the module does not use the patch
  (lookup) and translates the new text; the addon saves that translation with the new hash, and from
  then on apply leaves the row alone (the addon's hash differs from the patch's), until a version of
  the patch made from the new text is published;
- entries without src_hash (English never known for certain) are sent with the hash the addon has
  in cache.db for that key (0 if none: the addon asks for them once and the module answers with
  them, without a check);
- rows of cache.db with the patch text but src_hash 0 (written by v0.7, or moved to the v0.2.0
  layout by the addon) are not sent: the addon asks for them once and gets the patch with its hash;
- src_hash 0000000000000000 (lt_server.OUTDATED_HASH, stored as 0) marks a translation whose English
  changed and was not redone: never used, never sent.

The module writes into cache.db only to remove rows ('drop' keys, automatic texts that left the
patch, a failed string IDs check): the game shows that change at the next start.

Rules (unchanged from v0.4): reviewed > automatic (valid when made with the player's glossary)
> local translation; automatic rows purged by a glossary change are skipped until a patch made
with the new glossary arrives; 'drop' keys are removed once from cache.db; texts that left the
patch are removed when cache.db still holds what we wrote; a failed string IDs check takes the
patch out of cache.db and keeps it off until a check passes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
from pathlib import Path

import lt_server as lt

log = logging.getLogger("lt")

KIND_REVIEWED, KIND_AUTO, KIND_DROP = 0, 1, 2
CHUNK = 2000          # cache.db rows removed per transaction (short locks: the addon writes too)
PUSH_BLOCK = 2000     # patch texts sent to the addon in one go
PUSH_PAUSE = 0.2      # seconds between two blocks: below the addon's refresh_debounce (0.5 s), so
                      # the blocks of one update make a single refresh of the game texts
SQL_VARS = 500        # keys per "IN (...)" list
STALE_LOG_LIMIT = 20  # keys with a changed English text logged one by one per session
WAL_LIMIT = 1_000_000  # bytes patch_it.db-wal may keep after a checkpoint

SCHEMA = """
CREATE TABLE IF NOT EXISTS patch (
    key   INTEGER PRIMARY KEY NOT NULL,
    kind  INTEGER NOT NULL,      -- 0 reviewed (strings), 1 automatic (auto), 2 drop
    text  TEXT,                  -- NULL for drop
    src_hash INTEGER,            -- hash of the English text (lt_server.hash_int), 0 = outdated, or NULL
    piece INTEGER                -- piece number (key // width), NULL when loaded from a single file
);
CREATE INDEX IF NOT EXISTS patch_piece ON patch(piece);
CREATE TABLE IF NOT EXISTS pieces (name TEXT PRIMARY KEY NOT NULL, sha256 TEXT NOT NULL,
                                   entries INTEGER, size INTEGER);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY NOT NULL, v TEXT);
-- automatic texts written into cache.db, to remove them when they leave the patch
CREATE TABLE IF NOT EXISTS applied (key INTEGER PRIMARY KEY NOT NULL, text TEXT NOT NULL);
-- automatic keys purged by a glossary change (meta skip_version = patch version at that time)
CREATE TABLE IF NOT EXISTS skip (key INTEGER PRIMARY KEY NOT NULL);
-- 'drop' keys already removed from cache.db once
CREATE TABLE IF NOT EXISTS dropped (key INTEGER PRIMARY KEY NOT NULL);
"""

# "Show keys" mode (developers and helpers, file IT\show_keys.txt): texts in cache.db and
# answers start with their string ID, "44547 - Bambini", to find the key of a text seen in the
# game. Two modes:
#   "text" only texts with words: templates such as "[m]%str1%", "[null]" or "%str1% %str2%",
#          which the game uses to build names and dialogues out of several strings, stay as they are;
#   "all"  every non-empty text, templates included, to see how a text is built.
TAG_SKIP_RE = re.compile(r"%[A-Za-z]+\d*%|\[[^\]]*\]|<[^>]*>")
WORD_RE = re.compile(r"[^\W\d_]")


SHOW_MODES = ("text", "all")


def key_tag(key: int, text: str, mode: str | None) -> str:
    """The "<key> - " prefix for this text in this mode (None: mode off), or ""."""
    if mode == "all":
        return f"{key} - " if text.strip() else ""
    if mode == "text":
        return f"{key} - " if WORD_RE.search(TAG_SKIP_RE.sub("", text)) else ""
    return ""


def shown_text(key: int, text: str | None, mode: str | None) -> str | None:
    """The text with its key prefix, as it is in cache.db in this mode."""
    return text if text is None else key_tag(key, text, mode) + text


def untagged(key: int, text: str) -> str:
    tag = f"{key} - "
    return text[len(tag):] if text.startswith(tag) else text


def sync_key_tags(db: Path | None, cache_key: str, mode: str | None) -> int:
    """Put the texts of cache.db in the given mode ("text", "all" or None = no prefixes), CHUNK
    rows per transaction. Returns the rows changed. Done at every start, so cache.db always follows the
    mode: with the game closed (strumenti.bat) the change is visible at the very next start."""
    if not has_table(db):
        return 0
    con = sqlite3.connect(str(db), timeout=10, isolation_level=None)
    done, last = 0, -1
    try:
        while True:
            rows = con.execute("SELECT id, text FROM translations WHERE cache_key = ? AND id > ? "
                               f"ORDER BY id LIMIT {CHUNK}", (cache_key, last)).fetchall()
            if not rows:
                return done
            last = rows[-1][0]
            changes = []
            for key, text in rows:
                base = untagged(key, text)
                want = shown_text(key, base, mode)
                if want != text:
                    changes.append((want, cache_key, key))
            if changes:
                con.execute("BEGIN")
                try:
                    con.executemany("UPDATE translations SET text = ? WHERE cache_key = ? AND id = ?",
                                    changes)
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
                done += len(changes)
    finally:
        con.close()


def has_table(db: Path | None, need_hash: bool = False) -> bool:
    """True if the addon's cache.db exists and has its translations table; with need_hash, only
    in the layout of Text Translator v0.2.0 (src_hash column), the one the patch is written in. A
    cache.db of v0.1.x is moved to that layout by the addon itself when it loads the module."""
    if not db or not db.is_file():
        return False
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(translations)")}
    finally:
        con.close()
    return bool(cols) and (not need_hash or "src_hash" in cols)


def _chunks(items: list, size: int = SQL_VARS):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - damaged: ignored, as the old module did
        log.warning("%s unreadable: %s", path.name, exc)
        return None


class CachePatch:
    """The patch archive of the module (patch_it.db) and its application to cache.db."""

    def __init__(self, path: Path, cache_key: str, result_version: int) -> None:
        self.path = path
        self.cache_key, self.result_version = cache_key, result_version
        self.glossary: lt.Glossary | None = None  # the player's glossary (set by the module)
        self.show_keys: str | None = None  # "show keys" mode ("text", "all") or None
        # function(rows) that sends (key, u64 hash, text as shown) to the addon (set by the module);
        # None: nothing is sent (tools, tests)
        self.sender = None
        # texts sent in this session (key -> text): the addon saves them in cache.db a moment
        # later, so an apply that runs before (start, glossary and patch checks close together)
        # would send them again
        self._sent: dict[int, str] = {}
        self.lock = threading.RLock()             # one writer at a time: update, apply, withdraw
        self.rlock = threading.Lock()             # the reading connection (answers)
        self._stale_lock = threading.Lock()
        self._stale_seen: set[int] = set()  # keys found outdated in this session (log only)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = self._connect()
        self._upgrade()
        self.writer.executescript(SCHEMA)
        self.reader = self._connect()
        self._load_meta()
        self._shrink_wal()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=30, isolation_level=None,
                              check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute(f"PRAGMA journal_size_limit={WAL_LIMIT}")
        return con

    def _upgrade(self) -> None:
        """Archive of v0.5-v0.6 (column h, SHA-256 fingerprints, table stale): src_hash instead of
        h, and the pieces forgotten, so the next check downloads them all again."""
        con = self.writer
        cols = {r[1] for r in con.execute("PRAGMA table_info(patch)")}
        if "h" not in cols:
            return
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("ALTER TABLE patch DROP COLUMN h")
            con.execute("ALTER TABLE patch ADD COLUMN src_hash INTEGER")
            con.execute("DROP TABLE IF EXISTS stale")
            con.execute("DELETE FROM pieces")
            con.execute("DELETE FROM meta WHERE k = 'commit'")  # else "same commit": no download
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        log.info("patch archive moved to src_hash (all pieces downloaded again)")

    def _shrink_wal(self) -> None:
        """Move what patch_it.db-wal holds into patch_it.db and empty it. SQLite writes every
        change to the -wal file first; after a big write (first download, import, full apply) it
        would stay as big as that write (9 MB for 60k entries). Done at start too: the addon ends
        the module without closing the archive. If a reader is busy the file just stays as is."""
        try:
            self.writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            log.debug("patch_it.db-wal not emptied: %s", exc)

    def close(self) -> None:
        with self.lock, self.rlock:
            for con in (self.reader, self.writer):
                try:
                    con.close()
                except sqlite3.Error:
                    pass

    # -- meta -------------------------------------------------------------------------------
    def _load_meta(self) -> None:
        with self.rlock:
            m = dict(self.reader.execute("SELECT k, v FROM meta"))
        self.version = int(m.get("version") or 0)
        self.glossary_fp = m.get("glossary") or ""  # fingerprint of the glossary used for 'auto'
        self.width = int(m.get("width") or 0)
        self.commit = m.get("commit") or ""
        self.source = m.get("source") or ""          # pieces, github-file, file, patch-file
        self.skip_version = int(m["skip_version"]) if m.get("skip_version") else None
        self.migrated = bool(m.get("migrated"))
        self.file_sig = m.get("file_sig") or ""
        self._ids_failed = json.loads(m["ids_failed"]) if m.get("ids_failed") else None

    @staticmethod
    def _set_meta(con: sqlite3.Connection, **values) -> None:
        for k, v in values.items():
            if v is None:
                con.execute("DELETE FROM meta WHERE k = ?", (k,))
            else:
                con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, str(v)))

    def counts(self) -> dict[str, int]:
        with self.rlock:
            rows = self.reader.execute(
                "SELECT kind, count(*), count(src_hash) FROM patch GROUP BY kind").fetchall()
        out = {"reviewed": 0, "auto": 0, "drop": 0, "hash": 0}
        for kind, n, h in rows:
            out[("reviewed", "auto", "drop")[kind]] = n
            out["hash"] += h
        return out

    def pieces(self) -> dict[str, str]:
        with self.rlock:
            return dict(self.reader.execute("SELECT name, sha256 FROM pieces"))

    # -- validity of the automatic translations (same rules as lt_server.Patch) -----------------
    def auto_current(self) -> bool:
        """True if 'auto' was made with the player's glossary: every automatic row is valid."""
        g = self.glossary
        return bool(self.glossary_fp) and g is not None and self.glossary_fp == g.fingerprint

    def _skip_active(self) -> bool:
        """The skip table counts (lt_server.Patch._skipped): not with the player's glossary;
        with a fingerprinted patch until one made with the player's glossary arrives; without a
        fingerprint (older option 6) only for the version it was written with."""
        if self.skip_version is None or self.auto_current():
            return False
        return bool(self.glossary_fp) or self.skip_version == self.version

    def protected(self, keys: set[int]) -> set[int]:
        """Keys among these that a glossary purge must not touch: reviewed ones, and automatic
        ones when the patch was made with the player's glossary (was lt_server.Patch.keys())."""
        kinds = (KIND_REVIEWED, KIND_AUTO) if self.auto_current() else (KIND_REVIEWED,)
        out: set[int] = set()
        with self.rlock:
            for part in _chunks(sorted(keys)):
                out.update(k for (k,) in self.reader.execute(
                    f"SELECT key FROM patch WHERE kind IN ({','.join('?' * len(kinds))}) "
                    f"AND key IN ({','.join('?' * len(part))})", (*kinds, *part)))
        return out

    def suppress(self, keys: set[int]) -> None:
        """Remember automatic keys just purged from cache.db, so they are not filled again."""
        if not keys:
            return
        with self.lock:
            con = self.writer
            auto: set[int] = set()
            for part in _chunks(sorted(keys)):
                auto.update(k for (k,) in con.execute(
                    f"SELECT key FROM patch WHERE kind = {KIND_AUTO} "
                    f"AND key IN ({','.join('?' * len(part))})", part))
            if not auto:
                return
            before = {k for (k,) in con.execute("SELECT key FROM skip")} if self._skip_active() else set()
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute("DELETE FROM skip")
                con.executemany("INSERT INTO skip (key) VALUES (?)", [(k,) for k in sorted(before | auto)])
                self._set_meta(con, skip_version=self.version)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            self.skip_version = self.version

    # -- answers ----------------------------------------------------------------------------
    def lookup(self, key: int, src_hash: int) -> tuple[str | None, bool]:
        """(translation from the patch or None, True if the patch entry is outdated).

        An entry with src_hash is used only if it is the hash the addon sent with the English text
        (src_hash, the u64 of the protocol): otherwise ArenaNet changed the text after the
        translation was made, and the module translates it locally (see apply)."""
        with self.rlock:
            row = self.reader.execute("SELECT kind, text, src_hash FROM patch WHERE key = ?",
                                      (key,)).fetchone()
            if row is None or row[0] == KIND_DROP:
                return None, False
            kind, text, h = row
            if kind == KIND_AUTO and self._skip_active() and self.reader.execute(
                    "SELECT 1 FROM skip WHERE key = ?", (key,)).fetchone():
                return None, False
        if h is None or h == lt.hash_int(f"{src_hash:016x}"):
            return text, False
        with self._stale_lock:
            new = key not in self._stale_seen
            self._stale_seen.add(key)
            n = len(self._stale_seen)
        if new and n <= STALE_LOG_LIMIT:
            log.info("patch: the English text of key %d is not the one it was translated from: "
                     "translated locally", key)
        elif new and n == STALE_LOG_LIMIT + 1:
            log.info("patch: more keys with a changed English text, see the stats lines")
        return None, True

    # -- string IDs check -------------------------------------------------------------------
    def ids_failed(self) -> bool:
        return self._ids_failed is not None

    def _set_ids_failed(self, value: dict | None) -> None:
        con = self.writer
        con.execute("BEGIN IMMEDIATE")
        try:
            self._set_meta(con, ids_failed=None if value is None else json.dumps(value))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        self._ids_failed = value

    # -- cache.db ---------------------------------------------------------------------------
    def _shown(self, row: str) -> str:
        """SQL for the text of a patch (or applied) row as it is in cache.db."""
        return f"lt_shown({row}.key, {row}.text)" if self.show_keys else f"{row}.text"

    def _open_cache(self, db: Path) -> sqlite3.Connection:
        con = sqlite3.connect(str(db), timeout=10, isolation_level=None)
        mode = self.show_keys
        con.create_function("lt_shown", 2, lambda k, t: shown_text(k, t, mode), deterministic=True)
        con.execute("ATTACH DATABASE ? AS p", (str(self.path),))
        return con

    def _delete_rows(self, con: sqlite3.Connection, table: str) -> int:
        """Delete the keys of a temp table from cache.db, CHUNK rows per transaction, so the addon
        never waits long for cache.db."""
        done, last = 0, -1
        while True:
            rows = con.execute(f"SELECT key FROM temp.{table} WHERE key > ? ORDER BY key "
                               f"LIMIT {CHUNK}", (last,)).fetchall()
            if not rows:
                return done
            con.execute("BEGIN")
            try:
                con.executemany("DELETE FROM main.translations WHERE cache_key = ? AND id = ?",
                                [(self.cache_key, r[0]) for r in rows])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            done += len(rows)
            last = rows[-1][0]

    def _push(self, rows: list[tuple[int, int, str]]) -> int:
        """Send (key, signed src_hash, text) to the addon in blocks; returns the texts sent."""
        sent = 0
        for i in range(0, len(rows), PUSH_BLOCK):
            if i:
                time.sleep(PUSH_PAUSE)
            block = rows[i:i + PUSH_BLOCK]
            sent += self.sender([(k, h % (1 << 64), t) for k, h, t in block])
            self._sent.update((k, t) for k, _h, t in block)
        return sent

    def apply(self, db: Path | None, keys: set[int] | None = None) -> int:
        """Bring the game in line with the patch (all of it, or only `keys` after an update of
        some pieces): the rows of cache.db with another text are sent to the addon, which shows
        them at once and saves them; the rows to remove are deleted from cache.db.
        Returns the texts sent + the rows removed."""
        push: list[tuple[int, int, str]] = []
        with self.lock:
            with self.rlock:
                empty = self.reader.execute("SELECT 1 FROM patch LIMIT 1").fetchone() is None
                has_auto = self.reader.execute(
                    f"SELECT 1 FROM patch WHERE kind = {KIND_AUTO} LIMIT 1").fetchone() is not None
            if empty or not has_table(db, need_hash=True):
                return 0
            if self.ids_failed():
                log.warning("patch not applied: the string IDs did not match the map")
                return 0
            t0 = time.perf_counter()
            ck = self.cache_key
            con = self._open_cache(db)
            try:
                restrict = ""
                if keys is not None:
                    con.execute("CREATE TEMP TABLE k (key INTEGER PRIMARY KEY)")
                    con.executemany("INSERT OR IGNORE INTO temp.k VALUES (?)", [(x,) for x in keys])
                    restrict = " AND {0}.key IN (SELECT key FROM temp.k)"
                skip_on = 1 if has_auto and self._skip_active() else 0
                con.execute("CREATE TEMP TABLE w (key INTEGER PRIMARY KEY, text TEXT, h INTEGER, "
                            "kind INTEGER)")
                shown = self._shown("pt")
                # rows the addon holds with another text; never outdated entries (0), nor rows
                # the addon holds for another English text (its hash is not 0 and not ours: the
                # patch entry was made from an older text). Hash sent: the patch's, or the addon's
                # for entries without one.
                con.execute(
                    f"INSERT INTO temp.w SELECT pt.key, {shown}, coalesce(pt.src_hash, t.src_hash, 0), "
                    "pt.kind FROM p.patch pt "
                    "JOIN main.translations t ON t.cache_key = ? AND t.id = pt.key "
                    f"WHERE pt.kind IN ({KIND_REVIEWED}, {KIND_AUTO}) AND t.text IS NOT {shown} "
                    "AND pt.src_hash IS NOT 0 "
                    "AND NOT (pt.src_hash IS NOT NULL "
                    "AND coalesce(t.src_hash, 0) NOT IN (0, pt.src_hash)) "
                    f"AND NOT (pt.kind = {KIND_AUTO} AND ? AND pt.key IN (SELECT key FROM p.skip))"
                    + restrict.format("pt"), (ck, skip_on))
                con.execute("CREATE TEMP TABLE r (key INTEGER PRIMARY KEY)")
                if has_auto:  # automatic texts in cache.db that are no longer published
                    con.execute(
                        "INSERT OR IGNORE INTO temp.r SELECT a.key FROM p.applied a "
                        "JOIN main.translations t ON t.cache_key = ? AND t.id = a.key "
                        f"WHERE t.text = {self._shown('a')} AND NOT EXISTS (SELECT 1 FROM p.patch pt "
                        f"WHERE pt.key = a.key AND pt.kind IN ({KIND_REVIEWED}, {KIND_AUTO}))"
                        + restrict.format("a"), (ck,))
                new_drop = con.execute(
                    f"SELECT count(*) FROM p.patch WHERE kind = {KIND_DROP} "
                    "AND key NOT IN (SELECT key FROM p.dropped)").fetchone()[0]
                if new_drop:  # 'drop' keys not removed yet
                    con.execute(
                        "INSERT OR IGNORE INTO temp.r SELECT pt.key FROM p.patch pt "
                        "JOIN main.translations t ON t.cache_key = ? AND t.id = pt.key "
                        f"WHERE pt.kind = {KIND_DROP} AND pt.key NOT IN (SELECT key FROM p.dropped)"
                        + restrict.format("pt"), (ck,))
                if self.sender:
                    push = [r for r in con.execute("SELECT key, h, text FROM temp.w ORDER BY key")
                            if self._sent.get(r[0]) != r[2]]
                removed = self._delete_rows(con, "r")
                # registries, in patch_it.db
                con.execute("BEGIN")
                try:
                    clear_skip = self.auto_current() and self.skip_version is not None
                    if clear_skip:  # patch made with the player's glossary: old blocks not needed
                        con.execute("DELETE FROM p.skip")
                        con.execute("DELETE FROM p.meta WHERE k = 'skip_version'")
                    if new_drop:
                        con.execute("DELETE FROM p.dropped")
                        con.execute(f"INSERT INTO p.dropped SELECT key FROM p.patch "
                                    f"WHERE kind = {KIND_DROP}")
                    # automatic texts the game holds: already in cache.db, or sent now (the addon
                    # saves them a moment later)
                    if keys is None:
                        con.execute("DELETE FROM p.applied")
                    else:
                        con.execute("DELETE FROM p.applied WHERE key IN (SELECT key FROM temp.k)")
                    con.execute(
                        "INSERT INTO p.applied SELECT pt.key, pt.text FROM p.patch pt "
                        "JOIN main.translations t ON t.cache_key = ? AND t.id = pt.key "
                        f"WHERE pt.kind = {KIND_AUTO} AND t.text = {shown}"
                        + restrict.format("pt"), (ck,))
                    if push:
                        con.execute(
                            "INSERT OR REPLACE INTO p.applied SELECT pt.key, pt.text FROM temp.w w "
                            f"JOIN p.patch pt ON pt.key = w.key WHERE w.kind = {KIND_AUTO}")
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
                if clear_skip:
                    self.skip_version = None
            finally:
                con.close()
            if keys is None:  # the registry of all the automatic texts was rewritten
                self._shrink_wal()
            took = time.perf_counter() - t0
        # sent without holding the lock: answers and updates go on meanwhile
        sent = 0
        if push:
            try:
                sent = self._push(push)
            except Exception as exc:  # noqa: BLE001 - the addon asks again at the next start
                log.warning("patch v%d: texts not sent to the game: %s", self.version, exc)
            if sent:
                log.info("patch v%d: %d translations sent to the game", self.version, sent)
        if removed:
            log.info("patch v%d: %d old translations removed from cache.db (visible at next game "
                     "start)", self.version, removed)
        log.debug("patch compared with cache.db in %.2f s (%s)", took,
                  "all keys" if keys is None else f"{len(keys)} keys")
        return sent + removed

    def withdraw(self, db: Path | None, reason: str) -> int:
        """The string IDs do not match: remove from cache.db every row that holds a patch text
        (the addon asks for them again) and keep the patch off until a check passes."""
        with self.lock:
            self._set_ids_failed({"patch_version": self.version, "reason": reason,
                                  "time": int(time.time())})
            if not has_table(db):
                return 0
            con = self._open_cache(db)
            try:
                con.execute("CREATE TEMP TABLE r (key INTEGER PRIMARY KEY)")
                con.execute(
                    "INSERT OR IGNORE INTO temp.r SELECT t.id FROM main.translations t "
                    "JOIN p.patch pt ON pt.key = t.id "
                    f"WHERE t.cache_key = ? AND pt.kind IN ({KIND_REVIEWED}, {KIND_AUTO}) "
                    f"AND t.text = {self._shown('pt')}", (self.cache_key,))
                removed = self._delete_rows(con, "r")
                con.execute("DELETE FROM p.applied")
            finally:
                con.close()
        log.error("patch: %d patch texts removed from cache.db (string IDs do not match)", removed)
        return removed

    def ids_ok(self, db: Path | None) -> None:
        """A check passed: if the patch was off because of an earlier failed check, turn it on."""
        if not self.ids_failed():
            return
        with self.lock:
            self._set_ids_failed(None)
        log.info("string IDs match again: patch turned back on")
        self.apply(db)

    # -- loading ----------------------------------------------------------------------------
    @staticmethod
    def _rows_of(strings: dict[int, str], auto: dict[int, str], drop: set[int],
                 hashes: dict[int, str], piece: int | None) -> list[tuple]:
        """(key, kind, text, src_hash, piece) rows; src_hash as Text Translator keeps it (signed)."""
        hs = {k: lt.hash_int(v) for k, v in hashes.items()}
        rows = [(k, KIND_REVIEWED, v, hs.get(k), piece) for k, v in strings.items()]
        rows += [(k, KIND_AUTO, v, hs.get(k), piece) for k, v in auto.items()]
        rows += [(k, KIND_DROP, None, None, piece) for k in drop]
        return rows

    def _replace_all(self, con: sqlite3.Connection, data: dict, source: str, commit: str) -> None:
        """Load a whole single-file patch (patch_it.json format) in the open transaction."""
        version, strings, auto = lt.Patch.parse(data)
        drop = lt.Patch.parse_drop(data) - set(strings) - set(auto)
        fp = lt.Patch.parse_glossary(data)
        con.execute("DELETE FROM patch")
        con.execute("DELETE FROM pieces")
        con.executemany("INSERT INTO patch (key, kind, text, src_hash, piece) VALUES (?, ?, ?, ?, ?)",
                        self._rows_of(strings, auto, drop, {}, None))
        self._set_meta(con, version=version, glossary=fp, width=None, commit=commit or None,
                       source=source)

    def import_data(self, data: dict, source: str, commit: str = "") -> None:
        """Replace the whole patch with a single-file patch (validated first)."""
        with self.lock:
            con = self.writer
            con.execute("BEGIN IMMEDIATE")
            try:
                self._replace_all(con, data, source, commit)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            self._load_meta()
            self._shrink_wal()

    def import_file(self, path: Path, source: str) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.import_data(data, source)
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("patch %s unreadable: %s", path, exc)
            return False

    @staticmethod
    def _file_sig(path: Path) -> str:
        st = path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"

    def migrate(self, lang_dir: Path) -> None:
        """First start of v0.5: import patch_it.json and the JSON registries of v0.4 (left on disk,
        never read again). Later starts: import patch_it.json again if it changed and is not
        older (strumenti.bat option 7 copies the repository's patch there)."""
        pfile = lang_dir / "patch_it.json"
        if self.migrated:
            if not pfile.is_file():
                return
            sig = self._file_sig(pfile)
            if sig == self.file_sig:
                return
            data = _read_json(pfile)
            try:
                if isinstance(data, dict):
                    version = int(data.get("version", 0))
                    if version >= self.version or self.source == "patch-file":
                        self.import_data(data, "file")
                        log.info("local patch file imported: v%d (%s)", self.version, pfile.name)
                    else:
                        log.info("local patch file v%d ignored: older than v%d", version, self.version)
            except Exception as exc:  # noqa: BLE001
                log.warning("local patch file %s not imported: %s", pfile.name, exc)
            with self.lock:
                con = self.writer
                con.execute("BEGIN IMMEDIATE")
                self._set_meta(con, file_sig=sig)
                con.execute("COMMIT")
                self.file_sig = sig
            return

        t0 = time.perf_counter()
        data = _read_json(pfile) if pfile.is_file() else None
        applied = _read_json(lang_dir / "patch_it.applied.json")
        skip = _read_json(lang_dir / "patch_it.skip.json")
        dropped = _read_json(lang_dir / "patch_it.dropped.json")
        failed = _read_json(lang_dir / "patch_it.ids_failed.json")
        with self.lock:
            con = self.writer
            con.execute("BEGIN IMMEDIATE")
            try:
                if isinstance(data, dict):
                    try:
                        self._replace_all(con, data, "file", "")
                    except ValueError as exc:
                        log.warning("patch %s unreadable: %s", pfile.name, exc)
                if isinstance(applied, dict):
                    con.executemany("INSERT OR REPLACE INTO applied (key, text) VALUES (?, ?)",
                                    [(int(k), v) for k, v in applied.items()
                                     if str(k).isdigit() and isinstance(v, str)])
                if isinstance(skip, dict) and isinstance(skip.get("keys"), list):
                    try:
                        keys = {int(k) for k in skip["keys"]}
                        con.executemany("INSERT OR IGNORE INTO skip (key) VALUES (?)",
                                        [(k,) for k in keys])
                        self._set_meta(con, skip_version=int(skip.get("version", -1)))
                    except (TypeError, ValueError):
                        pass  # damaged: nothing skipped, as before
                if isinstance(dropped, list):
                    con.executemany("INSERT OR IGNORE INTO dropped (key) VALUES (?)",
                                    [(int(k),) for k in dropped if str(k).isdigit()])
                if (lang_dir / "patch_it.ids_failed.json").exists():
                    self._set_meta(con, ids_failed=json.dumps(failed if isinstance(failed, dict)
                                                              else {"reason": "migrated"}))
                self._set_meta(con, migrated=1,
                               file_sig=self._file_sig(pfile) if pfile.is_file() else None)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            self._load_meta()
            self._shrink_wal()
        c = self.counts()
        log.info("patch archive created from the v0.4 files in %.2f s: v%d, %d reviewed, "
                 "%d automatic, %d drop", time.perf_counter() - t0, self.version,
                 c["reviewed"], c["auto"], c["drop"])

    def update_pieces(self, index: dict, files: dict[str, Path], removed: list[str],
                      commit: str) -> set[int] | None:
        """Store the downloaded pieces (already checked against the index) and the index, in a
        single transaction: on any error nothing changes. Returns the keys whose entry changed,
        or None when the whole patch must be checked against cache.db again."""
        width = index["width"]
        with self.lock:
            con = self.writer
            full = self.source != "pieces" or self.width != width
            numbers = sorted({index["pieces"][n][0] for n in files}
                             | {lt.piece_number(n) for n in removed})
            old: dict[int, tuple] = {}
            new: dict[int, tuple] = {}
            con.execute("BEGIN IMMEDIATE")
            try:
                if full:
                    con.execute("DELETE FROM patch")
                    con.execute("DELETE FROM pieces")
                else:
                    for part in _chunks(numbers):
                        marks = ",".join("?" * len(part))
                        for k, kind, text, h in con.execute(
                                f"SELECT key, kind, text, src_hash FROM patch WHERE piece IN ({marks})",
                                part):
                            old[k] = (kind, text, h)
                        con.execute(f"DELETE FROM patch WHERE piece IN ({marks})", part)
                    con.executemany("DELETE FROM pieces WHERE name = ?",
                                    [(n,) for n in (*files, *removed)])
                for name in sorted(files):
                    number, sha, size = index["pieces"][name]
                    body = files[name].read_bytes()
                    if len(body) != size or hashlib.sha256(body).hexdigest() != sha:
                        raise ValueError(f"piece {name} does not match the index")
                    strings, auto, drop, hashes = lt.parse_piece(json.loads(body), number, width)
                    rows = self._rows_of(strings, auto, drop, hashes, number)
                    con.executemany("INSERT INTO patch (key, kind, text, src_hash, piece) "
                                    "VALUES (?, ?, ?, ?, ?)", rows)
                    con.execute("INSERT INTO pieces (name, sha256, entries, size) VALUES (?, ?, ?, ?)",
                                (name, sha, len(rows), size))
                    if not full:
                        new.update((r[0], (r[1], r[2], r[3])) for r in rows)
                self._set_meta(con, version=index["version"], glossary=index["glossary"],
                               width=width, commit=commit or None, source="pieces")
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            self._load_meta()
            self._shrink_wal()
        if full:
            return None
        return {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}


class PieceUpdater(threading.Thread):
    """Downloads the patch from GitHub: the index, then only the pieces whose sha256 changed,
    all at the same commit. If the repository has no index (older layout), the single file.

    raw_base and use_api exist for the tests (a local web server instead of GitHub)."""

    def __init__(self, patch: CachePatch, lang: str, db: Path | None,
                 branch: str = lt.DEFAULT_BRANCH, repo: str = lt.GITHUB_REPO,
                 raw_base: str | None = None, use_api: bool = True) -> None:
        super().__init__(daemon=True, name="patch-updater")
        self.patch, self.lang, self.db = patch, lang, db
        self.branch, self.repo, self.use_api = branch, repo, use_api
        self.raw_base = (raw_base or f"https://raw.githubusercontent.com/{repo}").rstrip("/")
        self.tmp = patch.path.with_name("patch_it.download")
        self.checks = 0

    def run(self) -> None:
        time.sleep(3)  # let the game finish loading first
        while True:
            try:
                self.check()
            except Exception as exc:  # noqa: BLE001 - never crash the thread
                log.info("patch update skipped: %s", exc)
            self.checks += 1
            time.sleep(lt.UPDATE_INTERVAL)

    def _newer_ok(self, version: int) -> bool:
        # never go back (GitHub may serve an older file for a few minutes), except over a test
        # patch loaded with --patch-file in an earlier session
        return version >= self.patch.version or self.patch.source == "patch-file"

    def check(self) -> str:
        """One update; returns what happened (for the tests and the log)."""
        p = self.patch
        commit = lt.latest_commit(self.repo, self.branch, "patch") if self.use_api else None
        if commit and commit == p.commit and p.source in ("pieces", "github-file"):
            # in the log once per start; the checks every UPDATE_INTERVAL only with --debug
            log.log(logging.DEBUG if self.checks else logging.INFO, "patch up to date (v%d)",
                    p.version)
            return "same commit"
        base = f"{self.raw_base}/{commit or self.branch}/patch/"
        try:
            index = lt.parse_index(json.loads(lt.http_get(base + f"{self.lang}/index.json",
                                                          limit=2_000_000)))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return self._single_file(base + f"patch_{self.lang}.json", commit or "")
            raise
        if not self._newer_ok(index["version"]):
            log.info("patch on GitHub (v%d) older than the local v%d: ignored",
                     index["version"], p.version)
            return "older"
        local = p.pieces()
        full = p.source != "pieces" or p.width != index["width"]
        want = [n for n, (_, sha, _) in index["pieces"].items() if full or local.get(n) != sha]
        removed = [] if full else sorted(n for n in local if n not in index["pieces"])
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True)
        try:
            files: dict[str, Path] = {}
            size = 0
            for name in want:
                _, sha, expected = index["pieces"][name]
                body = lt.http_get(base + f"{self.lang}/{name}", limit=expected)
                if len(body) != expected or hashlib.sha256(body).hexdigest() != sha:
                    raise ValueError(f"piece {name} does not match the index (incomplete or changed)")
                files[name] = self.tmp / name
                files[name].write_bytes(body)
                size += len(body)
            before = (p.glossary_fp, p.version)
            changed = p.update_pieces(index, files, removed, commit or "")
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
        if not want and not removed and before[1] == p.version:
            log.log(logging.DEBUG if self.checks else logging.INFO, "patch up to date (v%d)",
                    p.version)
        else:
            c = p.counts()
            log.info("patch updated: v%d, %d pieces downloaded (%d KB), %d removed; %d reviewed, "
                     "%d automatic, %d with the hash of the English", p.version, len(want),
                     size // 1024, len(removed), c["reviewed"], c["auto"], c["hash"])
        try:
            if changed is None or before[0] != p.glossary_fp:
                p.apply(self.db)
            elif changed:
                p.apply(self.db, changed)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot apply the patch to %s: %s", self.db, exc)
        return f"{len(want)} pieces, {len(removed)} removed"

    def _single_file(self, url: str, commit: str) -> str:
        """Repository without the pieces: the whole patch_<lang>.json, as up to v0.4."""
        p = self.patch
        data = json.loads(lt.http_get(url).decode("utf-8"))
        version = lt.Patch.parse(data)[0]
        if not self._newer_ok(version):
            log.info("patch on GitHub (v%d) older than the local v%d: ignored", version, p.version)
            return "older"
        p.import_data(data, "github-file", commit)
        c = p.counts()
        log.info("patch updated from the single file: v%d, %d reviewed, %d automatic",
                 p.version, c["reviewed"], c["auto"])
        try:
            p.apply(self.db)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot apply the patch to %s: %s", self.db, exc)
        return "single file"

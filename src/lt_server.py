"""Local translation server for the GW2 Nexus Local Translator addon.

Listens on 127.0.0.1:47831 and mimics the Google Translate mobile page that the
patched Japanese Text addon requests (GET /gtrans/?sl=en&tl=it&q=...).
Translation runs locally with CTranslate2 (OPUS-MT) on the CPU.

The glossary (fixed translations and protected terms) is refreshed from GitHub
in the background, so users never have to update anything by hand.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 47831
GLOSSARY_URL = (
    "https://raw.githubusercontent.com/NikeGipple/"
    "gw2-nexus-local-translator/main/glossary/glossary_it.json"
)
# Curated translations by string ID (key -> Italian), written into the addon's lang.db.
PATCH_URL = (
    "https://raw.githubusercontent.com/NikeGipple/"
    "gw2-nexus-local-translator/main/patch/patch_it.json"
)
# The model is published once as a GitHub Release (fixed tag, e.g. model-it-v1) and downloaded on first run.
MODEL_URL = (
    "https://github.com/NikeGipple/gw2-nexus-local-translator/"
    "releases/download/model-it-v1/opus-mt-en-it-ct2.zip"
)  # a matching "<url>.sha256" file is downloaded and checked too
UPDATE_INTERVAL = 6 * 3600  # seconds
SEP = "\n<%>\n"  # separator the addon puts between the texts of one batch
DEADLINE = 7.0  # seconds; the addon gives up after 10 s
# Game markup that must come out of the translation untouched: %str1%, %num1%, <lb>, <c=...>, </c> ...
TOKEN_RE = re.compile(r"%[A-Za-z]+\d*%|</?[A-Za-z][^<>\n]*>")

log = logging.getLogger("lt")


def app_dir() -> Path:
    """Folder that contains the exe (the Nexus 'addons' folder in a real install)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def work_dir() -> Path:
    """Our own subfolder for model, glossary cache and logs; created automatically."""
    return app_dir() / "Local_Translator"


def bundled_dir() -> Path:
    """Where files packed inside the exe live (PyInstaller onefile extracts them to a temp dir)."""
    return Path(getattr(sys, "_MEIPASS", app_dir()))


# --------------------------------------------------------------------------- #
# Glossary
# --------------------------------------------------------------------------- #
class Glossary:
    """exact:  whole-string overrides  {"Waypoint": "Waypoint"}
    terms:  words kept/translated as given inside sentences {"Lion's Arch": "Arco del Leone"}
    patterns: whole-line rules, {X} = one or more capitalized words (item prefixes)
              {"{X} Longbow": "Arco lungo {X}"}  ->  "Pact Longbow" = "Arco lungo Pact"
    """

    # {X}: capitalized words such as "Pact", "Strong Bandit", "Hunter's"
    WORDS = r"[A-Z][\w'’-]*(?: [A-Z][\w'’-]*)*"
    # markup around a whole line, e.g. <c=@reminder>Pact Longbow</c> or %str1%Pact Longbow%str2%
    WRAP_RE = re.compile(
        r"((?:<[^<>\n]*>|%[A-Za-z]+\d*%)*)(.*?)((?:<[^<>\n]*>|%[A-Za-z]+\d*%)*)", re.S)

    def __init__(self) -> None:
        self.exact: dict[str, str] = {}
        self.terms: list[tuple[str, str]] = []
        self.patterns: list[tuple[re.Pattern, str]] = []
        self.version = 0
        self._term_rx: dict[str, re.Pattern] = {}  # compiled term regexes, built once per term

    def term_regex(self, src: str) -> re.Pattern:
        """Whole-word regex for a glossary term, compiled only the first time it is needed.
        (Compiling ~10k regexes for every line made each batch take many seconds.)"""
        rx = self._term_rx.get(src)
        if rx is None:
            rx = self._term_rx[src] = re.compile(r"(?<!\w)" + re.escape(src) + r"(?!\w)")
        return rx

    def load_dict(self, data: dict) -> None:
        exact = data.get("exact", {})
        terms = data.get("terms", {})
        patterns = data.get("patterns", {})
        if not all(isinstance(x, dict) for x in (exact, terms, patterns)):
            raise ValueError("glossary: 'exact', 'terms' and 'patterns' must be objects")
        compiled = []
        for k, v in patterns.items():
            k, v = str(k), str(v)
            if "{X}" not in k:
                raise ValueError(f"glossary pattern without {{X}}: {k!r}")
            rx = re.escape(k).replace(re.escape("{X}"), f"(?P<X>{self.WORDS})")
            compiled.append((re.compile(rx), v))
        self.exact = {str(k): str(v) for k, v in exact.items()}
        terms = {str(k): str(v) for k, v in terms.items()}
        terms.update(self.case_variants(terms))
        # longest first, so "Lion's Arch Keep" wins over "Lion's Arch"
        self.terms = sorted(terms.items(), key=lambda kv: -len(kv[0]))
        self.patterns = sorted(compiled, key=lambda p: -len(p[0].pattern))
        self.version += 1

    @staticmethod
    def case_variants(terms: dict[str, str]) -> dict[str, str]:
        """Terms of two or more words also match in lower case and with only the first letter
        capitalized: "Raid God" covers "raid god" ("dio dei raid") and "Raid god" ("Dio dei raid").
        Single words stay case-sensitive on purpose: "Might" (the boon) must not touch the verb
        "might". An entry written explicitly in the glossary always wins over a variant.
        """
        out: dict[str, str] = {}
        for k, v in terms.items():
            if " " not in k.strip():
                continue
            low = k.lower()
            sentence = k[:1].upper() + k[1:].lower()
            if low != k and low not in terms:
                out[low] = v.lower()
            if sentence not in (k, low) and sentence not in terms:
                out[sentence] = v[:1].upper() + v[1:].lower()
        return out

    def lookup(self, line: str) -> str | None:
        """Fixed translation for a whole line (exact or pattern), or None."""
        if line in self.exact:
            return self.exact[line]
        if not self.patterns:
            return None
        pre, core, post = self.WRAP_RE.fullmatch(line).groups()
        if core in self.exact:
            return pre + self.exact[core] + post
        for rx, repl in self.patterns:
            m = rx.fullmatch(core)
            if m:
                return pre + repl.replace("{X}", m.group("X")) + post
        return None

    def load_file(self, path: Path) -> bool:
        try:
            self.load_dict(json.loads(path.read_text(encoding="utf-8")))
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("glossary %s unreadable: %s", path, exc)
            return False


class GlossaryUpdater(threading.Thread):
    """Downloads a JSON file from GitHub when it changes (ETag), validates it and applies it."""

    what = "glossary"
    filename = "glossary_it.json"

    def __init__(self, glossary: Glossary, data_dir: Path, on_change, url: str) -> None:
        super().__init__(daemon=True, name=f"{self.what}-updater")
        self.glossary, self.on_change, self.url = glossary, on_change, url
        self.path = data_dir / self.filename
        self.etag_path = self.path.with_suffix(".etag")

    def run(self) -> None:
        time.sleep(3)  # let the game finish loading first
        while True:
            try:
                self.check()
            except Exception as exc:  # noqa: BLE001 - never crash the thread
                log.info("%s update skipped: %s", self.what, exc)
            time.sleep(UPDATE_INTERVAL)

    def validate(self, data: dict) -> None:
        Glossary().load_dict(data)

    def apply(self, data: dict) -> None:
        self.glossary.load_dict(data)
        self.on_change()
        log.info("glossary updated (%d exact, %d terms)", len(self.glossary.exact), len(self.glossary.terms))

    RAW_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)")

    def current_url(self) -> str:
        """raw.githubusercontent.com caches a branch URL for ~5 minutes after a push. Ask the
        GitHub API for the branch's latest commit and download the file at that exact commit,
        so a restart right after a push already gets the new file. Falls back to the plain URL."""
        m = self.RAW_RE.fullmatch(self.url)
        if not m:
            return self.url
        owner, repo, branch, path = m.groups()
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}",
                headers={"User-Agent": "gw2-local-translator", "Accept": "application/vnd.github.sha"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                sha = resp.read(100).decode().strip()
            if re.fullmatch(r"[0-9a-f]{40}", sha):
                return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}"
        except Exception as exc:  # noqa: BLE001 - rate limit, offline...: use the branch URL
            log.debug("%s: latest commit unknown (%s), using %s", self.what, exc, self.url)
        return self.url

    def check(self) -> None:
        req = urllib.request.Request(self.current_url(), headers={"User-Agent": "gw2-local-translator"})
        if self.etag_path.exists() and self.path.exists():
            req.add_header("If-None-Match", self.etag_path.read_text().strip())
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read(5_000_000)
                etag = resp.headers.get("ETag", "")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                log.info("%s up to date", self.what)
                return
            raise
        data = json.loads(body.decode("utf-8"))
        self.validate(data)  # validate before replacing anything
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, self.path)
        if etag:
            self.etag_path.write_text(etag)
        self.apply(data)


# --------------------------------------------------------------------------- #
# Patch: curated translations by string ID, shared through GitHub
# --------------------------------------------------------------------------- #
# The addon (Japanese Text, modified) keeps its translations in lang.db, table
#   strings(key INTEGER PRIMARY KEY, jp TEXT, seed INTEGER, updated TIMESTAMP)
# key  = the game's internal string ID (same for every player)
# jp   = the translation shown in game; the addon never overwrites a non-empty one
# seed = a per-string value sent by the game server: private, never read or shared by us
# The patch only contains key -> Italian: no English game text, no seed.
class Patch:
    """Two sections, both key -> Italian:

    strings  reviewed translations: always written, replace whatever lang.db has, and are never
             removed by a glossary change.
    auto     machine translations made centrally, shipped so new users do not wait for them:
             only fill empty rows, and stay removable by a glossary change. A row removed that way
             is not filled again until a new patch version (built with the new glossary) arrives.
    drop     keys that were in 'auto' in an older version and are no longer published: their old
             text is removed once from lang.db, so the addon asks again (and the glossary applies).
    """

    def __init__(self, skip_path: Path | None = None) -> None:
        self.strings: dict[int, str] = {}
        self.auto: dict[int, str] = {}
        self.drop: set[int] = set()
        self.version = 0
        self.lock = threading.Lock()
        self.skip_path = skip_path  # auto keys purged since this version: {"version": n, "keys": [...]}

    @staticmethod
    def _section(data: dict, name: str, required: bool) -> dict[int, str]:
        section = data.get(name, None if required else {})
        if not isinstance(section, dict):
            raise ValueError(f"patch: '{name}' must be an object {{key: text}}")
        out = {}
        for k, v in section.items():
            if not str(k).isdigit() or not isinstance(v, str) or not v.strip():
                raise ValueError(f"patch: bad entry {k!r} in '{name}'")
            out[int(k)] = v
        return out

    @classmethod
    def parse(cls, data: dict) -> tuple[int, dict[int, str], dict[int, str]]:
        strings = cls._section(data, "strings", True)
        auto = {k: v for k, v in cls._section(data, "auto", False).items() if k not in strings}
        return int(data.get("version", 0)), strings, auto

    @staticmethod
    def parse_drop(data: dict) -> set[int]:
        drop = data.get("drop", [])
        if not isinstance(drop, list) or not all(str(k).isdigit() for k in drop):
            raise ValueError("patch: 'drop' must be a list of keys")
        return {int(k) for k in drop}

    def load_dict(self, data: dict) -> None:
        version, strings, auto = self.parse(data)
        drop = self.parse_drop(data) - set(strings) - set(auto)
        with self.lock:
            self.version, self.strings, self.auto, self.drop = version, strings, auto, drop

    def load_file(self, path: Path) -> bool:
        try:
            self.load_dict(json.loads(path.read_text(encoding="utf-8")))
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("patch %s unreadable: %s", path, exc)
            return False

    def to_dict(self) -> dict:
        with self.lock:
            data = {"version": self.version,
                    "strings": {str(k): self.strings[k] for k in sorted(self.strings)},
                    "auto": {str(k): self.auto[k] for k in sorted(self.auto)}}
            if self.drop:
                data["drop"] = sorted(self.drop)
            return data

    def keys(self) -> set[int]:
        """Reviewed keys: protected from glossary purges."""
        with self.lock:
            return set(self.strings)

    # -- auto keys removed by a glossary change -------------------------------------------
    def _skipped(self) -> set[int]:
        if not self.skip_path:
            return set()
        try:
            data = json.loads(self.skip_path.read_text(encoding="utf-8"))
            if int(data.get("version", -1)) == self.version:
                return {int(k) for k in data.get("keys", [])}
        except Exception:  # noqa: BLE001 - missing or damaged: nothing skipped
            pass
        return set()

    def suppress(self, keys: set[int]) -> None:
        """Remember auto keys just purged from lang.db, so they are not filled again."""
        with self.lock:
            keys = keys & set(self.auto)
        if not keys or not self.skip_path:
            return
        skipped = self._skipped() | keys
        try:
            self.skip_path.write_text(json.dumps({"version": self.version, "keys": sorted(skipped)}),
                                      encoding="utf-8")
        except OSError as exc:
            log.warning("cannot save %s: %s", self.skip_path, exc)

    def _applied_path(self) -> Path | None:
        return self.skip_path.with_name("patch_it.applied.json") if self.skip_path else None

    def _read_applied(self) -> dict[int, str]:
        """Automatic texts this server already wrote into lang.db (key -> text)."""
        path = self._applied_path()
        try:
            return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        except Exception:  # noqa: BLE001 - missing or damaged: nothing known
            return {}

    def _dropped_path(self) -> Path | None:
        return self.skip_path.with_name("patch_it.dropped.json") if self.skip_path else None

    def _read_dropped(self) -> set[int]:
        """'drop' keys already removed from lang.db once (never removed twice)."""
        path = self._dropped_path()
        try:
            return {int(k) for k in json.loads(path.read_text(encoding="utf-8"))}
        except Exception:  # noqa: BLE001 - missing or damaged: nothing removed yet
            return set()

    def apply(self, db: Path | None) -> int:
        """Write the patch into the addon's lang.db. Returns the number of rows changed.

        reviewed: always written.
        auto:     written into empty rows, and over rows that still hold the automatic text this
                  server wrote earlier (so a newer patch version updates them); a row whose text
                  changed locally is left alone.
        The table is created by the addon itself: if lang.db is not there yet nothing is done
        (the map watcher retries as soon as it appears). The seed column is never touched.
        """
        with self.lock:
            reviewed = list(self.strings.items())
            auto = list(self.auto.items())
            drop = set(self.drop)
        if not (reviewed or auto or drop) or not db or not db.is_file():
            return 0
        import sqlite3
        con = sqlite3.connect(str(db), timeout=10)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strings'").fetchone():
                return 0
            current = dict(con.execute("SELECT key, jp FROM strings"))
            skipped = self._skipped() if auto else set()
            applied = self._read_applied()
            write_reviewed = [(k, v) for k, v in reviewed if current.get(k) != v]
            write_auto = []
            for k, v in auto:
                now_text = current.get(k)
                if now_text == v or k in skipped:
                    continue
                if now_text is None or now_text == applied.get(k):
                    write_auto.append((k, v))
            # automatic texts no longer published: remove the old text so the addon asks again.
            # 1) keys this server wrote itself and that left the patch (text still unchanged)
            published = {k for k, _ in reviewed} | {k for k, _ in auto}
            remove = {k for k, t in applied.items()
                      if k not in published and current.get(k) == t} if auto else set()
            # 2) keys listed in 'drop' (older patch versions), removed only once
            dropped_before = self._read_dropped()
            remove |= {k for k in drop - dropped_before
                       if k not in published and current.get(k) is not None}
            now = int(time.time())
            with con:
                con.executemany(
                    "INSERT INTO strings (key, jp, updated) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET jp = excluded.jp, updated = excluded.updated",
                    [(k, v, now) for k, v in write_reviewed + write_auto])
                con.executemany("DELETE FROM strings WHERE key = ?", [(k,) for k in remove])
        finally:
            con.close()
        for k in remove:
            current.pop(k, None)
        dpath = self._dropped_path()
        if dpath and drop - dropped_before:
            try:
                dpath.write_text(json.dumps(sorted(drop)), encoding="utf-8")
            except OSError as exc:
                log.warning("cannot save %s: %s", dpath, exc)
        # remember which automatic texts are now in lang.db, for the next patch version
        final = dict(current)
        final.update(write_reviewed + write_auto)
        path = self._applied_path()
        if path and auto:
            try:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({str(k): v for k, v in auto if final.get(k) == v},
                                          ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
            except OSError as exc:
                log.warning("cannot save %s: %s", path, exc)
        if write_reviewed or write_auto:
            log.info("patch v%d: %d reviewed and %d automatic translations written to %s",
                     self.version, len(write_reviewed), len(write_auto), db)
        if remove:
            log.info("patch v%d: %d old automatic translations removed from %s",
                     self.version, len(remove), db)
        return len(write_reviewed) + len(write_auto) + len(remove)


class PatchUpdater(GlossaryUpdater):
    what = "patch"
    filename = "patch_it.json"

    def __init__(self, patch: Patch, data_dir: Path, db: Path | None, url: str) -> None:
        self.patch, self.db = patch, db
        super().__init__(None, data_dir, None, url)  # type: ignore[arg-type]

    def validate(self, data: dict) -> None:
        version = Patch.parse(data)[0]
        if version < self.patch.version:
            # GitHub serves raw files from a cache for a few minutes after a push: never go back
            raise ValueError(f"downloaded v{version} is older than the local v{self.patch.version}")

    def apply(self, data: dict) -> None:
        self.patch.load_dict(data)
        log.info("patch updated: v%d, %d reviewed, %d automatic", self.patch.version,
                 len(self.patch.strings), len(self.patch.auto))
        try:
            self.patch.apply(self.db)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot apply the patch to %s: %s", self.db, exc)


# --------------------------------------------------------------------------- #
# Local map: string ID <-> English <-> Italian (private, never shared)
# --------------------------------------------------------------------------- #
def enable_wal(db: Path) -> None:
    """Switch the addon's lang.db to WAL journaling (a setting saved inside the file).
    With the default journal, the server reading lang.db blocks the addon's writes and the
    addon logs "database is locked"; in WAL mode readers and the writer no longer block
    each other. Harmless if it fails: it is simply tried again at the next start."""
    import sqlite3
    try:
        con = sqlite3.connect(str(db), timeout=10)
        try:
            mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        finally:
            con.close()
        if str(mode).lower() == "wal":
            log.debug("addon database in WAL mode (%s)", db)
        else:
            log.warning("addon database: cannot enable WAL mode (mode is %s)", mode)
    except Exception as exc:  # noqa: BLE001 - never block the server for this
        log.warning("addon database: cannot enable WAL mode: %s", exc)


def norm(text: str) -> str:
    return html.unescape(text).strip()


class KeyMap:
    """Learns which English text belongs to each string ID.

    The addon sends only text to the server and stores only key + translation in lang.db.
    The server remembers what it has just answered (Italian -> English); a watcher then reads
    the rows the addon writes into lang.db and joins them by their Italian text.
    The result (map_it.db) stays on this PC: it contains English game text.
    """

    RECENT_TTL = 600  # seconds an answer is kept to be matched
    POLL = 5          # seconds between two reads of lang.db

    def __init__(self, path: Path, addon_db: Path | None, patch: Patch | None = None) -> None:
        import sqlite3
        self.path, self.addon_db, self.patch = path, addon_db, patch
        self.con = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self.con.execute("""CREATE TABLE IF NOT EXISTS texts (
            key INTEGER PRIMARY KEY NOT NULL,
            en TEXT NOT NULL,
            it TEXT,
            how TEXT,          -- 'live' (seen while playing) or 'backfill' (rebuilt from the cache)
            seen INTEGER)""")
        self.con.commit()
        self.lock = threading.Lock()
        self.recent: dict[str, tuple[str, float]] = {}  # Italian -> (English, time)
        self.ambiguous: dict[str, float] = {}           # Italian answered for two different texts
        self.since = int(time.time()) - 1               # lang.db rows older than this: backfill
        self.done_at_since: set[int] = set()
        self.db_seen = False

    # called by the engine for every text it answers
    def remember(self, en: str, it: str) -> None:
        en, it = norm(en), norm(it)
        if not en or not it:
            return
        now = time.time()
        with self.lock:
            old = self.recent.get(it)
            if old and old[0] != en:
                self.ambiguous[it] = now
            self.recent[it] = (en, now)

    def _expire(self) -> None:
        limit = time.time() - self.RECENT_TTL
        with self.lock:
            for d in (self.recent, self.ambiguous):
                for k in [k for k, v in d.items() if (v[1] if isinstance(v, tuple) else v) < limit]:
                    del d[k]

    def _save(self, rows: list[tuple[int, str, str, str]]) -> int:
        """rows: (key, en, it, how). A 'live' row always wins over a 'backfill' one."""
        if not rows:
            return 0
        now = int(time.time())
        with self.lock:
            with self.con:
                before = self.con.total_changes
                self.con.executemany(
                    "INSERT INTO texts (key, en, it, how, seen) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET en = excluded.en, it = excluded.it, "
                    "how = excluded.how, seen = excluded.seen "
                    "WHERE excluded.how = 'live' OR texts.how = 'backfill'",
                    [(k, en, it, how, now) for k, en, it, how in rows])
                return self.con.total_changes - before

    def poll(self) -> int:
        """Read the rows the addon added since the last call and match them. Returns rows saved."""
        db = self.addon_db
        if not db or not db.is_file():
            self.db_seen = False
            return 0
        if not self.db_seen:  # lang.db just appeared (first start, or the user deleted it)
            self.db_seen = True
            enable_wal(db)
            if self.patch:
                try:
                    self.patch.apply(db)
                except Exception as exc:  # noqa: BLE001
                    log.warning("cannot apply the patch to %s: %s", db, exc)
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            rows = con.execute(
                "SELECT key, jp, updated FROM strings WHERE jp IS NOT NULL AND updated >= ?",
                (self.since,)).fetchall()
        finally:
            con.close()
        patched = self.patch.keys() if self.patch else set()
        found = []
        newest = self.since
        with self.lock:
            for key, jp, updated in rows:
                updated = int(updated or 0)
                if updated == self.since and key in self.done_at_since:
                    continue
                if updated > newest:
                    newest = updated
                if key in patched:
                    continue
                it = norm(jp)
                hit = self.recent.get(it)
                if hit and it not in self.ambiguous:
                    found.append((key, hit[0], jp, "live"))
            if newest > self.since:
                self.since, self.done_at_since = newest, set()
            self.done_at_since |= {k for k, _, u in rows if int(u or 0) == self.since}
        saved = self._save(found)
        if saved:
            log.debug("map: %d strings matched", saved)
        return saved

    def backfill(self, cache: dict[str, str], glossary: Glossary) -> int:
        """Rebuild the English text of rows already in lang.db from the server cache (per line).

        Only rows where every line maps back to exactly one English line are saved.
        """
        db = self.addon_db
        if not db or not db.is_file():
            return 0
        reverse: dict[str, str | None] = {}
        for src in (cache, glossary.exact):
            for en, it in src.items():
                it = norm(it)
                reverse[it] = None if (it in reverse and reverse[it] != en) else en
        with self.lock:
            known = {k for (k,) in self.con.execute("SELECT key FROM texts")}
        patched = self.patch.keys() if self.patch else set()
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            rows = con.execute("SELECT key, jp FROM strings WHERE jp IS NOT NULL").fetchall()
        finally:
            con.close()
        found = []
        for key, jp in rows:
            if key in known or key in patched:
                continue
            lines = []
            for line in html.unescape(jp).split("\n"):
                en = reverse.get(line.strip())
                if en is None:
                    if line.strip() in reverse or Engine._needs_translation(line):
                        break  # ambiguous or unknown: give up on this row
                    en = line  # numbers, markup, empty lines: same in both languages
                lines.append(en)
            else:
                found.append((key, "\n".join(lines), jp, "backfill"))
        saved = self._save(found)
        log.info("map backfill: %d of %d existing translations matched", saved, len(rows))
        return saved

    def run_watcher(self) -> None:
        while True:
            try:
                self.poll()
                self._expire()
            except Exception as exc:  # noqa: BLE001 - never crash the thread
                log.debug("map watcher: %s", exc)
            time.sleep(self.POLL)

    def count(self) -> int:
        with self.lock:
            return self.con.execute("SELECT count(*) FROM texts").fetchone()[0]


# --------------------------------------------------------------------------- #
# Translators
# --------------------------------------------------------------------------- #
class FakeTranslator:
    """Echo translator, used with --fake to test the whole chain without a model."""

    def translate(self, texts: list[str]) -> list[str]:
        return [f"[IT] {t}" for t in texts]


class CT2Translator:
    def __init__(self, model_dir: Path, threads: int) -> None:
        import ctranslate2  # imported lazily so --fake works without it
        import sentencepiece as spm

        self.tr = ctranslate2.Translator(
            str(model_dir), device="cpu", inter_threads=1, intra_threads=threads
        )
        self.sp_src = spm.SentencePieceProcessor(str(model_dir / "source.spm"))
        self.sp_tgt = spm.SentencePieceProcessor(str(model_dir / "target.spm"))
        self.lock = threading.Lock()

    def translate(self, texts: list[str]) -> list[str]:
        # Marian/OPUS-MT models need the end-of-sentence token on the source side. Without it
        # the model never stops (up to 512 tokens) and produces garbage, about 100x slower.
        tokens = [self.sp_src.encode(t, out_type=str)[:400] + ["</s>"] for t in texts]
        longest = max(len(t) for t in tokens)
        with self.lock:
            results = self.tr.translate_batch(
                tokens, beam_size=2, max_decoding_length=min(512, longest * 2 + 16)
            )
        return [self.sp_tgt.decode(r.hypotheses[0]) for r in results]


def ensure_model(model_dir: Path, data_dir: Path, url: str) -> None:
    """Download and unpack the model on first run (no-op if it is already there)."""
    if (model_dir / "model.bin").is_file():
        return
    log.info("model not found, downloading %s", url)
    headers = {"User-Agent": "gw2-local-translator"}
    sha_req = urllib.request.Request(url + ".sha256", headers=headers)
    with urllib.request.urlopen(sha_req, timeout=30) as resp:
        expected = resp.read(1024).decode().split()[0].lower()

    part = data_dir / "model.zip.part"
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp, \
            open(part, "wb") as out:
        while chunk := resp.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != expected:
        part.unlink(missing_ok=True)
        raise RuntimeError("model checksum mismatch")

    tmp = model_dir.with_name(model_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(part) as zf:
        for member in zf.namelist():  # guard against paths escaping the folder
            if not (tmp / member).resolve().is_relative_to(tmp.resolve()):
                raise RuntimeError(f"unsafe path in model zip: {member}")
        zf.extractall(tmp)
    part.unlink(missing_ok=True)
    shutil.rmtree(model_dir, ignore_errors=True)
    os.replace(tmp, model_dir)
    log.info("model installed in %s", model_dir)


class Pending(Exception):
    """Some texts are still being translated in the background: the client should retry."""


class Engine:
    """Translates batches of texts separated by SEP.

    A single background worker translates missing lines (short requests first) and stores them
    in a cache that is also saved on disk. A request waits up to DEADLINE seconds; if not all of
    its lines are ready it raises Pending (answered with 503) while the worker keeps going, so the
    addon's retry finds everything in the cache.
    """

    BATCH = 24

    def __init__(self, translator, glossary: Glossary, cache_path: Path | None = None,
                 addon_db: Path | None = None, patch: "Patch | None" = None) -> None:
        self.translator, self.glossary = translator, glossary  # translator may be None until loaded
        self.cache: dict[str, str] = {}
        self.queue: deque[str] = deque()
        self.queued: set[str] = set()
        self.cv = threading.Condition()
        self.cache_path = cache_path
        self.addon_db = addon_db  # the addon translation store (traduzione_it/lang.db)
        # glossary terms the cache was built with, to invalidate only what changes
        self.snapshot_path = cache_path.with_suffix(".glossary.json") if cache_path else None
        self.gen = 0  # bumped at every glossary change; batches started before it are discarded
        self.done_count = 0
        self.keymap: KeyMap | None = None  # told about every answered text, to learn string IDs
        self.patch = patch                 # its rows in lang.db are never purged
        self._load_cache()
        self.sync_glossary()
        threading.Thread(target=self._work, daemon=True, name="translator-worker").start()

    # -- cache ------------------------------------------------------------------------------
    def _load_cache(self) -> None:
        if not self.cache_path or not self.cache_path.is_file():
            return
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                for row in f:
                    try:
                        item = json.loads(row)
                        self.cache[item["s"]] = item["t"]
                    except Exception:  # noqa: BLE001 - skip a damaged line
                        continue
            log.info("cache loaded: %d translations", len(self.cache))
        except OSError as exc:
            log.warning("cache unreadable: %s", exc)

    def _append_cache(self, results: dict[str, str]) -> None:
        if not self.cache_path:
            return
        try:
            with open(self.cache_path, "a", encoding="utf-8") as f:
                for src, dst in results.items():
                    f.write(json.dumps({"s": src, "t": dst}, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("cannot save cache: %s", exc)

    def _rewrite_cache(self) -> None:
        if not self.cache_path:
            return
        tmp = self.cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for src, dst in self.cache.items():
                f.write(json.dumps({"s": src, "t": dst}, ensure_ascii=False) + "\n")
        os.replace(tmp, self.cache_path)

    def _read_snapshot(self) -> dict | None:
        """Glossary the cache was built with: {"terms": {...}, "exact": {...}} or None if unknown."""
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - missing or damaged: treated as unknown
            return None
        if "terms" not in data or not isinstance(data["terms"], dict):  # old format: terms only
            data = {"terms": data, "exact": None}
        return {"terms": {str(k): str(v) for k, v in data["terms"].items()},
                "exact": None if data.get("exact") is None else
                {str(k): str(v) for k, v in data["exact"].items()}}

    def sync_glossary(self) -> None:
        """Called at startup and when the glossary changes.

        Compares the glossary with the one the cache was built with and drops only the cached
        lines that contain a term that was added, removed or changed; everything else is kept.
        The old translations of those lines are also removed from the addon's own database
        (lang.db), otherwise the addon would keep showing them and never ask again.
        """
        new_terms = dict(self.glossary.terms)
        new_exact = dict(self.glossary.exact)
        purge: set[str] = set()  # old Italian texts to remove from the addon database
        with self.cv:
            self.gen += 1
            old = self._read_snapshot() if self.snapshot_path else None
            if old is None:
                # cache built with an unknown glossary (first run of this version): start clean
                changed = None
                dropped = len(self.cache)
                self.cache.clear()
            else:
                old_terms = old["terms"]
                changed = {k for k in old_terms.keys() | new_terms.keys()
                           if old_terms.get(k) != new_terms.get(k)}
                old_exact = old["exact"] if old["exact"] is not None else new_exact
                exact_changed = {k for k in old_exact.keys() | new_exact.keys()
                                 if old_exact.get(k) != new_exact.get(k)}
                for k in exact_changed:
                    if k in old_exact:
                        purge.add(old_exact[k])
                    elif k in self.cache:  # was translated by the model before
                        purge.add(self.cache.pop(k))
                dropped = 0
                if changed and self.cache:
                    hit = re.compile("|".join(re.escape(k) for k in
                                              sorted(changed, key=len, reverse=True)))
                    stale = [s for s in self.cache if hit.search(s)]
                    for s in stale:
                        purge.add(self.cache.pop(s))
                    dropped = len(stale)
                changed = changed | exact_changed
            try:
                if dropped or purge or (changed is None and self.cache_path
                                        and self.cache_path.exists()):
                    self._rewrite_cache()
                if self.snapshot_path and (changed is None or changed or old["exact"] is None):
                    tmp = self.snapshot_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"terms": new_terms, "exact": new_exact},
                                              ensure_ascii=False, indent=0), encoding="utf-8")
                    os.replace(tmp, self.snapshot_path)
            except OSError as exc:
                log.warning("cannot save cache after glossary change: %s", exc)
        if changed is None:
            log.info("glossary sync: cache reset (%d lines)", dropped)
        elif changed:
            log.info("glossary sync: %d entries changed, %d cached lines dropped, %d kept",
                     len(changed), dropped, len(self.cache))
        if purge:
            self._purge_addon_db(purge)

    def _purge_addon_db(self, texts: set[str]) -> None:
        """Delete from the addon's lang.db every stored translation containing one of these texts.

        The addon keys its rows by the game's string ID, which the server does not receive, so
        rows are found by their Italian text. Deleting a few rows too many is harmless: the addon
        just asks again and the server answers from its cache. Rows that come from the patch
        (curated translations) are never deleted.
        """
        db = self.addon_db
        texts = {t for t in texts if len(t.strip()) >= 3}
        if not db or not texts or not db.is_file():
            return
        keep = self.patch.keys() if self.patch else set()
        try:
            import sqlite3
            con = sqlite3.connect(str(db), timeout=10)
            try:
                with con:
                    doomed: set[int] = set()
                    for t in texts:
                        doomed.update(k for (k,) in con.execute(
                            "SELECT key FROM strings WHERE jp IS NOT NULL AND instr(jp, ?) > 0", (t,)))
                    doomed -= keep
                    if self.patch:
                        self.patch.suppress(doomed)
                    con.executemany("DELETE FROM strings WHERE key = ?", [(k,) for k in doomed])
                    total = len(doomed)
            finally:
                con.close()
            log.info("addon database: %d old translations removed (%s)", total, db)
        except Exception as exc:  # noqa: BLE001 - never block the server for this
            log.warning("cannot clean the addon database %s: %s", db, exc)

    # -- request side -----------------------------------------------------------------------
    @staticmethod
    def _needs_translation(line: str) -> bool:
        return re.search(r"[A-Za-z]{2}", TOKEN_RE.sub("", line)) is not None

    def translate(self, text: str) -> str:
        if not text.strip():
            return text
        g = self.glossary
        entries = [e.split("\n") for e in text.split(SEP)]
        needed: list[str] = []
        seen: set[str] = set()
        with self.cv:
            for lines in entries:
                for line in lines:
                    if (line in seen or line in self.cache or g.lookup(line) is not None
                            or not self._needs_translation(line)):
                        continue
                    seen.add(line)
                    needed.append(line)

            if needed:
                small = len(needed) <= 300  # short requests jump the queue
                for line in needed:
                    if line not in self.queued:
                        self.queued.add(line)
                        (self.queue.appendleft if small else self.queue.append)(line)
                self.cv.notify_all()
                end = time.monotonic() + DEADLINE
                while not all(line in self.cache for line in needed):
                    left = end - time.monotonic()
                    if left <= 0:
                        raise Pending(f"{sum(l not in self.cache for l in needed)} of {len(needed)} lines pending")
                    self.cv.wait(left)

            def result(line: str) -> str:
                fixed = g.lookup(line)
                if fixed is not None:
                    return fixed
                return self.cache.get(line, line)

            outs = ["\n".join(result(line) for line in lines) for lines in entries]
        if self.keymap:
            for lines, out in zip(entries, outs):
                self.keymap.remember("\n".join(lines), out)
        return SEP.join(outs)

    # -- worker side ------------------------------------------------------------------------
    def _work(self) -> None:
        while True:
            with self.cv:
                while not self.queue or self.translator is None:
                    self.cv.wait(1.0)
                batch = [self.queue.popleft() for _ in range(min(self.BATCH, len(self.queue)))]
                gen = self.gen
            try:
                results = self._translate_lines(batch)
            except Exception:  # noqa: BLE001
                log.exception("translation of a batch failed")
                results = {}
            with self.cv:
                if gen != self.gen:
                    # glossary changed while translating: redo this batch with the new terms
                    self.queue.extendleft(reversed(batch))
                    self.cv.notify_all()
                    continue
                self.cache.update(results)
                for line in batch:
                    self.queued.discard(line)
                self.done_count += len(results)
                self._append_cache(results)  # under the lock, so it never races a rewrite
                self.cv.notify_all()
            if results and (self.done_count // 500) != ((self.done_count - len(results)) // 500):
                log.info("translated %d lines so far, %d waiting", self.done_count, len(self.queue))

    def _protect(self, line: str) -> tuple[str, dict[str, str]]:
        """Swap game markup and glossary terms for placeholders so the model leaves them alone."""
        restore: dict[str, str] = {}

        def stash(match: re.Match) -> str:
            ph = f"QZ{len(restore)}QZ"
            restore[ph] = match.group(0)
            return ph

        protected = TOKEN_RE.sub(stash, line)
        g = self.glossary
        for src, dst in g.terms:
            if src not in protected:  # cheap check first: almost no term occurs in a given line
                continue
            pattern = g.term_regex(src)

            def stash_term(match: re.Match, dst: str = dst) -> str:
                ph = f"QZ{len(restore)}QZ"
                restore[ph] = dst
                return ph

            protected = pattern.sub(stash_term, protected)
        return protected, restore

    def _translate_lines(self, lines: list[str]) -> dict[str, str]:
        results: dict[str, str] = {}
        prepared = [self._protect(line) for line in lines]
        outs = self.translator.translate([p for p, _ in prepared])
        for line, (_, restore), out in zip(lines, prepared, outs):
            if not all(ph in out for ph in restore):
                results[line] = line  # a placeholder got lost: keep the English text, never break markup
                continue
            for ph, original in restore.items():
                out = out.replace(ph, original)
            results[line] = out
        return results


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_handler(engine: Engine):
    state = {"requests": 0, "warned_503": False, "pending": 0}

    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalTranslator/1.0"
        MAX_REQUEST_LINE = 8_000_000  # the stdlib default (64 KB) is too small for big GET batches

        def handle_one_request(self):  # same as the stdlib version, with a larger line limit
            try:
                self.raw_requestline = self.rfile.readline(self.MAX_REQUEST_LINE + 1)
                if len(self.raw_requestline) > self.MAX_REQUEST_LINE:
                    self.requestline = self.request_version = self.command = ""
                    self.send_error(414)
                    return
                if not self.raw_requestline:
                    self.close_connection = True
                    return
                if not self.parse_request():
                    return
                method = getattr(self, "do_" + self.command, None)
                if method is None:
                    self.send_error(501, f"Unsupported method ({self.command!r})")
                    return
                method()
                self.wfile.flush()
            except TimeoutError:
                self.close_connection = True

        def log_message(self, fmt, *args):  # route access logs to our logger
            log.debug("%s %s", self.address_string(), fmt % args)

        def _reply(self, text: str | None, status: int = 200) -> None:
            body = (
                "<!DOCTYPE html><html><body>"
                f'<div class="result-container">{html.escape(text or "", quote=False)}</div>'
                "</body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, params: dict[str, list[str]]) -> None:
            path = urlparse(self.path).path
            if not path.startswith("/gtrans"):
                self._reply("", 404)
                return
            if engine.translator is None:  # model still downloading/loading: ask the client to retry
                if not state["warned_503"]:
                    state["warned_503"] = True
                    log.warning("request received but the model is not ready yet (answering 503)")
                self._reply("", 503)
                return
            q = (params.get("q") or [""])[0]
            state["requests"] += 1
            if state["requests"] <= 15:  # first requests logged in detail, to check the addon's format
                log.info("request #%d %s len=%d lines=%d q=%r", state["requests"], self.command,
                         len(q), q.count("\n") + 1, q[:300])
            tl = (params.get("tl") or ["it"])[0]
            if tl != "it":
                log.info("unexpected target language %r (only 'it' is supported)", tl)
            log.debug("request q=%r", q[:120])
            try:
                self._reply(engine.translate(q))
            except Pending as pending:
                state["pending"] += 1
                if state["pending"] <= 10 or state["pending"] % 50 == 0:
                    log.info("not ready yet, client should retry (%s) [#%d]", pending, state["pending"])
                self._reply("", 503)
            except Exception:  # noqa: BLE001
                log.exception("translation failed")
                self._reply(q, 500)

        def do_GET(self):  # noqa: N802
            self._handle(parse_qs(urlparse(self.path).query))

        def do_POST(self):  # noqa: N802
            length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            body = self.rfile.read(length).decode("utf-8", "replace")
            params = parse_qs(urlparse(self.path).query)
            params.update(parse_qs(body))
            self._handle(params)

    return Handler


# --------------------------------------------------------------------------- #
# Developer commands: review the map in a spreadsheet, build the patch
# --------------------------------------------------------------------------- #
REVIEW_COLUMNS = ["key", "inglese", "italiano_attuale", "italiano_patch", "nuova_traduzione"]


def export_review(map_path: Path, patch_path: Path, out: Path, addon_db: Path | None = None) -> int:
    """CSV for Excel/LibreOffice (';' separated, UTF-8). Fill 'nuova_traduzione' and import it.

    One row for every translation the game has (lang.db) or the patch has; 'italiano_attuale' is
    what players will see once the patch is published. Any text seen in game can be found by its
    Italian text; 'inglese' is empty when the map does not know it yet (for example texts
    filled by the patch, which the addon never sends to the server).
    Contains English game text: keep it on your PC, never publish it.
    """
    import csv
    import sqlite3
    patch = Patch()
    patch.load_file(patch_path)
    english: dict[int, str] = {}
    italian: dict[int, str] = {}
    if map_path.is_file():
        con = sqlite3.connect(f"file:{map_path}?mode=ro", uri=True)
        try:
            for k, en, it in con.execute("SELECT key, en, it FROM texts"):
                english[k] = en
                italian[k] = it or ""
        finally:
            con.close()
    if addon_db and addon_db.is_file():
        con = sqlite3.connect(f"file:{addon_db}?mode=ro", uri=True)
        try:  # the current text in game wins over the one remembered by the map
            italian.update(con.execute("SELECT key, jp FROM strings WHERE jp IS NOT NULL"))
        finally:
            con.close()
    # ...and the patch you are about to publish wins over both: after option 6 the new automatic
    # translations are only in the patch file, not yet in the game
    italian.update(patch.auto)
    italian.update(patch.strings)
    keys = sorted(english.keys() | italian.keys() | patch.strings.keys())
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(REVIEW_COLUMNS)
        for k in keys:
            w.writerow([k, english.get(k, ""), italian.get(k, ""), patch.strings.get(k, ""), ""])
    return len(keys)


def _write_patch(patch: Patch, path: Path) -> None:
    data = patch.to_dict()
    Patch.parse(data)  # never write an invalid file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def import_review(review: Path, patch_path: Path) -> tuple[int, int]:
    """Merge the 'nuova_traduzione' column into the reviewed section of the patch.

    A single '-' removes the entry. Returns (added or changed, removed). The patch version goes
    up by one if anything changed.
    """
    import csv
    patch = Patch()
    patch.load_file(patch_path)
    changed = removed = 0
    with open(review, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        for row in csv.DictReader(f, dialect=dialect):
            key, new = (row.get("key") or "").strip(), row.get("nuova_traduzione") or ""
            if not key.isdigit() or not new.strip():
                continue
            k = int(key)
            if new.strip() == "-":
                if patch.strings.pop(k, None) is not None:
                    removed += 1
            elif patch.strings.get(k) != new:
                patch.strings[k] = new
                patch.auto.pop(k, None)
                changed += 1
    if changed or removed:
        patch.version += 1
        _write_patch(patch, patch_path)
    return changed, removed


class Progress:
    """One console line: done/total, percentage, speed and time left. Silent without a console."""

    def __init__(self, total: int, label: str) -> None:
        self.total, self.label, self.done = total, label, 0
        self.start = time.monotonic()
        self.last = 0.0
        self.out = sys.stdout

    @staticmethod
    def _hms(sec: float) -> str:
        sec = int(sec)
        h, m = sec // 3600, sec % 3600 // 60
        return f"{h} h {m:02d} min" if h else f"{m} min {sec % 60:02d} s"

    def add(self, n: int) -> None:
        self.done += n
        now = time.monotonic()
        if self.out and (now - self.last > 1 or self.done >= self.total):
            self.last = now
            spent = now - self.start
            pct = self.done * 100 / self.total if self.total else 100
            left = spent / self.done * (self.total - self.done) if self.done else 0
            self.out.write(f"\r  {self.label}: {self.done}/{self.total} ({pct:.0f}%)"
                           f"  trascorso {self._hms(spent)}  rimanente ~{self._hms(left)}   ")
            self.out.flush()
        if self.done >= self.total and self.out:
            self.out.write("\n")
            self.out.flush()


def build_auto(map_path: Path, patch_path: Path, glossary: Glossary, translations: dict[str, str],
               translate_lines, label: str = "traduzione", batch: int = 0) -> tuple[int, int, int]:
    """Rebuild the 'auto' section by translating again the English text of every known string
    (map) with the current glossary.

    translations     line -> Italian already available (cache); missing lines are translated
    translate_lines  function(list of lines) -> {line: Italian}; it also saves its own cache,
                     so an interrupted run continues where it stopped
    Rows that would stay identical to the English text are skipped: they add nothing and would
    publish English game text. Strings without English in the map keep their old entry.

    Returns (rows in auto, rows skipped, lines translated now).
    """
    import sqlite3
    patch = Patch()
    patch.load_file(patch_path)
    m = sqlite3.connect(f"file:{map_path}?mode=ro", uri=True)
    try:
        english = {k: en for k, en in m.execute("SELECT key, en FROM texts") if k not in patch.strings}
    finally:
        m.close()
    g = glossary
    needed = sorted({line for en in english.values() for line in en.split("\n")
                     if line not in translations and g.lookup(line) is None
                     and Engine._needs_translation(line)})
    log.info("build-auto: %d strings, %d lines to translate", len(english), len(needed))
    if sys.stdout:
        print(f"  {len(english)} frasi note, {len(needed)} righe da tradurre "
              f"(le altre sono gia' pronte)", flush=True)
    progress = Progress(len(needed), label)
    done = 0
    step = batch or Engine.BATCH
    for i in range(0, len(needed), step):
        chunk = needed[i:i + step]
        results = translate_lines(chunk)
        translations.update(results)
        done += len(results)
        progress.add(len(chunk))
        if (i // step) % 20 == 0:
            log.info("build-auto: %d of %d lines translated", done, len(needed))

    def result(line: str) -> str:
        fixed = g.lookup(line)
        return fixed if fixed is not None else translations.get(line, line)

    auto = {k: v for k, v in patch.auto.items() if k not in english and k not in patch.strings}
    skipped = 0
    for key, en in english.items():
        lines = en.split("\n")
        outs = [result(line) for line in lines]
        if "\n".join(outs).strip() == en.strip() or any(
                o.strip() == e.strip() and Engine._needs_translation(e) for e, o in zip(lines, outs)):
            skipped += 1  # still (partly) in English: names kept as they are, failed lines
            continue
        auto[key] = "\n".join(outs)
    # keys leaving 'auto' go into 'drop': players still holding their old text get it removed
    drop = (patch.drop | (set(patch.auto) - set(auto))) - set(auto) - set(patch.strings)
    if auto != patch.auto or drop != patch.drop:
        patch.auto = auto
        patch.drop = drop
        patch.version += 1
        _write_patch(patch, patch_path)
    return len(auto), skipped, done


def run_build_auto(args, map_path: Path) -> str:
    model = args.model or args.lang_dir / "model"
    if not (model / "model.bin").is_file():
        return f"model not found: {model}"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(args.lang_dir.parent / "lt-server.log", maxBytes=500_000,
                                                      backupCount=1, encoding="utf-8")])
    glossary = Glossary()
    if not (args.glossary_file and glossary.load_file(args.glossary_file)):
        if not glossary.load_file(args.lang_dir / "glossary_it.json"):
            glossary.load_file(bundled_dir() / "glossary_it.default.json")
    threads = max(1, (os.cpu_count() or 2) // 2)
    # same cache and lang.db as the game server: lines whose glossary terms changed are dropped
    # from both, exactly as the server does when it receives a new glossary
    engine = Engine(CT2Translator(model, threads=threads), glossary,
                    args.lang_dir / "cache_it.jsonl", args.addon_db)
    def translate_lines(batch: list[str]) -> dict[str, str]:
        results = engine._translate_lines(batch)
        engine.cache.update(results)
        engine._append_cache(results)
        return results

    n, skipped, translated = build_auto(map_path, args.patch_file, glossary, engine.cache,
                                        translate_lines, "OPUS-MT")
    return (f"patch {args.patch_file}: {n} automatic translations "
            f"({translated} lines translated now, {skipped} strings left out)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="echo translator, no model needed")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--data", type=Path, default=work_dir(), help="shared folder: logs, fake.txt")
    ap.add_argument("--lang-dir", type=Path, default=work_dir() / "IT",
                    help="per-language folder: model/ and the glossary")
    ap.add_argument("--model", type=Path, default=None, help="default: <lang-dir>/model")
    ap.add_argument("--addon-db", type=Path, default=app_dir() / "traduzione_it" / "lang.db",
                    help="the addon translation database, cleaned when the glossary changes")
    ap.add_argument("--glossary-url", default=GLOSSARY_URL)
    ap.add_argument("--model-url", default=MODEL_URL)
    ap.add_argument("--patch-url", default=PATCH_URL)
    ap.add_argument("--patch-file", type=Path, default=None,
                    help="use this local patch instead of downloading it (developers)")
    ap.add_argument("--no-patch", action="store_true", help="do not write the patch into lang.db")
    ap.add_argument("--export-review", type=Path, metavar="CSV",
                    help="write the ID/English/Italian map to a CSV for review, then exit")
    ap.add_argument("--import-review", type=Path, metavar="CSV",
                    help="merge the 'nuova_traduzione' column of a reviewed CSV into the patch, then exit")
    ap.add_argument("--build-auto", action="store_true",
                    help="translate again every known string for the patch 'auto' section "
                         "(needs --patch-file; uses the model), then exit")
    ap.add_argument("--glossary-file", type=Path, default=None,
                    help="glossary to use instead of the downloaded one (developers: the repository file)")
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    map_path = args.lang_dir / "map_it.db"
    dev_patch = args.patch_file or args.lang_dir / "patch_it.json"
    if args.export_review or args.import_review or args.build_auto:
        args.lang_dir.mkdir(parents=True, exist_ok=True)
        if args.export_review:
            n = export_review(map_path, dev_patch, args.export_review, args.addon_db)
            msg = f"{n} rows written to {args.export_review}"
        elif not args.patch_file:
            msg = "--import-review and --build-auto need --patch-file (e.g. the repository's patch\\patch_it.json)"
        elif args.build_auto:
            if not map_path.is_file():
                msg = f"map not found: {map_path}"
            else:
                msg = run_build_auto(args, map_path)
        else:
            changed, removed = import_review(args.import_review, args.patch_file)
            msg = f"patch {args.patch_file}: {changed} added/changed, {removed} removed"
        print(msg) if sys.stdout else None
        (args.lang_dir / "last_command.txt").write_text(msg + "\n", encoding="utf-8")
        return 0

    args.data.mkdir(parents=True, exist_ok=True)
    args.lang_dir.mkdir(parents=True, exist_ok=True)
    if args.model is None:
        args.model = args.lang_dir / "model"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(args.data / "lt-server.log", maxBytes=500_000, backupCount=1, encoding="utf-8")
    ]
    if sys.stderr:  # no console when frozen with --noconsole
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    glossary = Glossary()
    if not glossary.load_file(args.lang_dir / "glossary_it.json"):
        glossary.load_file(bundled_dir() / "glossary_it.default.json")

    fake = args.fake or (args.data / "fake.txt").is_file()  # fake.txt = test mode without model
    if fake:
        log.info("TEST MODE: answering with [IT] prefix, no real translation")
    # Patch first: the engine must know its keys before the startup glossary sync purges lang.db.
    patch = Patch(skip_path=args.lang_dir / "patch_it.skip.json")
    if not args.no_patch:
        patch.load_file(dev_patch)  # last downloaded copy, or the developer's local file
    engine = Engine(FakeTranslator() if fake else None, glossary, args.lang_dir / "cache_it.jsonl",
                    args.addon_db, patch=patch)
    try:
        keymap = KeyMap(map_path, args.addon_db, None if args.no_patch else patch)
        engine.keymap = keymap

        def map_startup() -> None:
            try:
                keymap.poll()  # writes the patch into lang.db right away, if lang.db exists
                keymap.backfill(dict(engine.cache), glossary)
            except Exception as exc:  # noqa: BLE001
                log.warning("map backfill failed: %s", exc)
            log.info("map: %d strings known (%s)", keymap.count(), map_path)
            keymap.run_watcher()  # also writes the patch as soon as lang.db exists

        threading.Thread(target=map_startup, daemon=True, name="map-watcher").start()
    except Exception as exc:  # noqa: BLE001 - the map is optional
        log.warning("map disabled: %s", exc)

    def load_model() -> None:
        try:
            ensure_model(args.model, args.lang_dir, args.model_url)
            threads = max(1, min(4, (os.cpu_count() or 2) // 2))
            engine.translator = CT2Translator(args.model, threads=threads)
            log.info("model loaded")
        except Exception:  # noqa: BLE001
            log.exception("could not prepare the translation model in %s", args.model)

    if not fake:
        threading.Thread(target=load_model, daemon=True, name="model-loader").start()

    try:
        httpd = ThreadingHTTPServer((HOST, args.port), make_handler(engine))
    except OSError as exc:
        log.error("cannot bind %s:%d (%s) - another instance is probably running", HOST, args.port, exc)
        return 3

    if not args.no_update:
        GlossaryUpdater(glossary, args.lang_dir, engine.sync_glossary, args.glossary_url).start()
        if not args.no_patch and not args.patch_file:
            PatchUpdater(patch, args.lang_dir, args.addon_db, args.patch_url).start()

    log.info("ready on http://%s:%d/gtrans/", HOST, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

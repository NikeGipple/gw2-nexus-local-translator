"""Shared code of Local Translator: glossary, patch, OPUS-MT engine and local map.

Imported as a library by lt_module.py (the module started by Ideka's Text Translator addon)
and by the developer tools. Translation runs locally with CTranslate2 (OPUS-MT) on the CPU.
The glossary (fixed translations and protected terms) and the patch (curated translations by
string ID) are refreshed from GitHub in the background, so users never have to update anything
by hand.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections import deque
from pathlib import Path

import plurale_it  # Italian plural markers for names (same folder)

GITHUB_REPO = "NikeGipple/gw2-nexus-local-translator"
DEFAULT_BRANCH = "main"  # the module can read another branch with --branch (tests)


def raw_url(path: str, branch: str = DEFAULT_BRANCH) -> str:
    """URL of a file of the repository on raw.githubusercontent.com."""
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{branch}/{path}"


GLOSSARY_URL = raw_url("glossary/glossary_it.json")
# Curated translations by string ID (key -> translation), applied by the module. Since v0.5 the
# module reads the patch split in pieces (patch/<lang>/index.json + pNNNN.json, see split_patch);
# this single file is still published for the modules up to v0.4.
PATCH_URL = raw_url("patch/patch_it.json")
# The model is published once as a GitHub Release (fixed tag, e.g. model-it-v1) and downloaded on first run.
MODEL_URL = (
    "https://github.com/NikeGipple/gw2-nexus-local-translator/"
    "releases/download/model-it-v1/opus-mt-en-it-ct2.zip"
)  # a matching "<url>.sha256" file is downloaded and checked too
UPDATE_INTERVAL = 6 * 3600  # seconds
# Largest glossary or patch accepted (GitHub refuses files over 100 MB). The old limit of 5 MB cut
# bigger files: the JSON could not be read and the update failed with only "update skipped".
MAX_DOWNLOAD = 100_000_000
# Game markup that must come out of the translation untouched: %str1%, %num1%, <lb>, <c=...>, </c>,
# and the markers of the raw game strings such as [s], [the], [null], [pl:"Foci"], [f:"Goddess"]
# (Text Translator sends them as they are; a broken marker can crash the game).
TOKEN_RE = re.compile(r'%[A-Za-z]+\d*%|</?[A-Za-z][^<>\n]*>|\[[^\[\]\n]*\]')
# Version of the glossary protection rules, saved with the cache snapshot. When it grows, the
# cached lines translated with the old rules are dropped once (see Engine.sync_glossary).
#   2 = glossary terms glued to markup ("Large Bone[s]", "Plaza of<br>Dwayna") are protected
#   3 = English plural markers resolved before translating ("Edible Mushroom[s]" -> "Edible
#       Mushrooms"): kept in the Italian text, the game added an English "s" ("Funghi commestibilis")
#   4 = a single "%" in the translation becomes "%%" (fix_percent): GW2 formats texts like printf
#       and a lone "%" cuts the text after it
#   5 = names with plural markers (no %num%) are translated in the SINGULAR and get Italian plural
#       markers: 'Medaglia[pl:"Medaglie"] precisa[pl:"precise"]' (see plural_name)
PROTECT_VERSION = 5
# Plural markers of the raw game strings: "Piece[s]" adds "s", "Box[pl:\"Boxes\"]" replaces the
# word before it. The game applies [pl:"..."] to the translated text too (checked in game on
# 2026-10-09: 'Gamberetto[pl:"Gamberetti"]' shows "Gamberetto" for 1 item, "Gamberetti" for more,
# and two markers in one string work), but "[s]" would add an English "s" ("Funghi commestibilis").
PLURAL_RE = re.compile(r'(?<=\w)\[s\]|[\w\'’-]+\[pl:"([^"\[\]\n]*)"\]')
# Plural rules of the module language (Italian: plurale_it.make_plural). The language is set in
# lt_module (CACHE_KEY "it", IT\ folder, *_it files). Another language needs its own rules, a
# plurale_<lang>.py with the same make_plural(singular, english, plural_or_None) -> (text, outcome,
# changes), or None: then names are translated in the plural form as before, without markers.
PLURAL_RULES = plurale_it.make_plural
# Plural markers written by the rules in a translation
IT_PL_RE = re.compile(r'\[pl:"[^"\[\]\n]*"\]')
NUM_RE = re.compile(r"%num\d*%")


def resolve_plural(line: str) -> str:
    """English plural form of a raw game string: "Piece[s] of Gear" -> "Pieces of Gear",
    "Recovered Tool Box[pl:\"Boxes\"]" -> "Recovered Tool Boxes"."""
    return PLURAL_RE.sub(lambda m: "s" if m.group(1) is None else m.group(1), line)


def english_singular(line: str) -> str:
    """English singular form: "Piece[s] of Gear" -> "Piece of Gear",
    "Recovered Tool Box[pl:\"Boxes\"]" -> "Recovered Tool Box"."""
    return PLURAL_RE.sub(lambda m: "" if m.group(1) is None else m.group(0).split("[pl:")[0], line)


def plural_name(line: str) -> bool:
    """A name with English plural markers ("Medaglia" items, "Chicken[s]"): translated in the
    singular + Italian plural markers. Sentences with %num% stay in the plural form: the English
    singular ("Win %num2% rated arena game") confuses the models."""
    return (PLURAL_RULES is not None and PLURAL_RE.search(line) is not None
            and NUM_RE.search(line) is None)


def italian_plural(line: str, singular_en: str, out: str, plural_out: str | None = None) -> str:
    """Translation of a plural_name line, made from its singular translation `out`.
    Returns the raw English line if the name stayed in English (materials kept in English by the
    glossary: the game shows the right form with the original markers), the singular with Italian
    plural markers when the rules are sure, otherwise the singular as it is."""
    core = out.strip()
    if core == singular_en.strip() or core == resolve_plural(line).strip():
        return line
    res, esito, _ = PLURAL_RULES(core, singular_en + " " + resolve_plural(line), plural_out)
    if esito.startswith("dubbio") or IT_PL_RE.sub("", res) != core:
        return out
    lead = out[:len(out) - len(out.lstrip())]
    return lead + res + out[len(out.rstrip()):]
# Control characters: a string that contains them is undecoded game data, not text
# (e.g. key 508192 arrived as "\x8e6]2T\x93..."); it is never translated nor published.
GARBLED_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# "%%" (literal %), game codes %num1% / %str1% (also glued: %str1%%str2%) or a lone "%"
PERCENT_RE = re.compile(r"%[A-Za-z]+\d*%|%%|%")


def fix_percent(text: str, english: str | None = None) -> str:
    """GW2 formats texts like printf: "%%" is a literal %, "%num1%" a game value. A lone "%" in
    the translation ("aumenta del 5% la forza") breaks the text, so it becomes "%%".
    With the English text: nothing is touched when the English has no "%" or is undecoded data."""
    if not text or "%" not in text:
        return text
    if english is not None and ("%" not in english or GARBLED_RE.search(english)):
        return text
    return PERCENT_RE.sub(lambda m: "%%" if m.group(0) == "%" else m.group(0), text)


def bad_percent(text: str, english: str | None = None) -> bool:
    """True if fix_percent would change the text."""
    return fix_percent(text, english) != text

log = logging.getLogger("lt")


def app_dir() -> Path:
    """Folder that contains the exe (for the module: addons\\text_translator\\modules\\local_translator_it)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_dir() -> Path:
    """Where files bundled by PyInstaller live (the _internal folder of the --onedir build)."""
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
        self.fingerprint = ""  # identifies the glossary content (see fingerprint_of)
        self._term_rx: dict[str, re.Pattern] = {}  # compiled term regexes, built once per term

    def term_regex(self, src: str) -> re.Pattern:
        """Whole-word regex for a glossary term, compiled only the first time it is needed.
        (Compiling ~10k regexes for every line made each batch take many seconds.)

        Used on lines where the game markup is already replaced by placeholders (QZ0QZ), so a
        placeholder glued to the term also counts as a word boundary: "Large Bone[s]" becomes
        "Large BoneQZ0QZ" and "Plaza of<br>Dwayna" becomes "Plaza ofQZ0QZDwayna"."""
        rx = self._term_rx.get(src)
        if rx is None:
            rx = self._term_rx[src] = re.compile(
                r"(?:(?<!\w)|(?<=\dQZ))" + re.escape(src) + r"(?:(?!\w)|(?=QZ\d))")
        return rx

    @staticmethod
    def old_term_regex(src: str) -> re.Pattern:
        """Boundaries used up to v0.2.0: a term glued to markup was not protected. Only used once,
        to find the cached lines translated with that bug (Engine.sync_glossary)."""
        return re.compile(r"(?<!\w)" + re.escape(src) + r"(?!\w)")

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
        self.fingerprint = self.fingerprint_of(data)
        self.version += 1

    @staticmethod
    def fingerprint_of(data: dict) -> str:
        """Short code computed from the content of exact, terms and patterns (not from the order
        of the entries nor from "_comment"). Option 6 writes it into the patch ("glossary"), so the
        module knows whether the automatic translations were made with the glossary it has."""
        content = {name: {str(k): str(v) for k, v in data.get(name, {}).items()}
                   for name in ("exact", "terms", "patterns")}
        raw = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

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


def latest_commit(repo: str, branch: str, what: str = "download") -> str | None:
    """SHA of the latest commit of a branch (GitHub API), or None (rate limit, offline...)."""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/commits/{branch}",
            headers={"User-Agent": "gw2-local-translator", "Accept": "application/vnd.github.sha"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            sha = resp.read(100).decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    except Exception as exc:  # noqa: BLE001 - rate limit, offline...: the caller uses the branch
        log.debug("%s: latest commit of %s unknown (%s)", what, branch, exc)
    return None


def http_get(url: str, limit: int = MAX_DOWNLOAD, timeout: int = 30) -> bytes:
    """Download a file (gzip-compressed on the wire when the server allows it, ~3x smaller for
    the patch). Raises urllib.error.HTTPError (e.g. 404) and ValueError for files larger than
    `limit` or cut short."""
    req = urllib.request.Request(url, headers={"User-Agent": "gw2-local-translator",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(limit + 1)
        length = resp.headers.get("Content-Length")
        encoding = (resp.headers.get("Content-Encoding") or "").strip().lower()
    if len(raw) > limit:
        raise ValueError(f"file larger than {limit} bytes")
    if length and length.strip().isdigit() and int(length) != len(raw):
        raise ValueError(f"download incomplete ({len(raw)} of {int(length)} bytes)")
    if encoding == "gzip":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        body = d.decompress(raw, limit + 1)
        if len(body) > limit or d.unconsumed_tail:
            raise ValueError(f"file larger than {limit} bytes")
        if not d.eof:
            raise ValueError("download incomplete (gzip data cut short)")
        return body
    if encoding not in ("", "identity"):
        raise ValueError(f"unexpected Content-Encoding {encoding!r}")
    return raw


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
        log.info("glossary updated: %s (%d exact, %d terms)", self.glossary.fingerprint,
                 len(self.glossary.exact), len(self.glossary.terms))

    RAW_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)")

    def current_url(self) -> str:
        """raw.githubusercontent.com caches a branch URL for ~5 minutes after a push. Ask the
        GitHub API for the branch's latest commit and download the file at that exact commit,
        so a restart right after a push already gets the new file. Falls back to the plain URL."""
        m = self.RAW_RE.fullmatch(self.url)
        if not m:
            return self.url
        owner, repo, branch, path = m.groups()
        sha = latest_commit(f"{owner}/{repo}", branch, self.what)
        if sha:
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}"
        return self.url

    def check(self) -> None:
        req = urllib.request.Request(self.current_url(), headers={"User-Agent": "gw2-local-translator"})
        if self.etag_path.exists() and self.path.exists():
            req.add_header("If-None-Match", self.etag_path.read_text().strip())
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read(MAX_DOWNLOAD + 1)
                etag = resp.headers.get("ETag", "")
                length = resp.headers.get("Content-Length")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                log.info("%s up to date", self.what)
                return
            raise
        if len(body) > MAX_DOWNLOAD:
            raise ValueError(f"file larger than {MAX_DOWNLOAD} bytes")
        if length and length.strip().isdigit() and int(length) != len(body):
            raise ValueError(f"download incomplete ({len(body)} of {int(length)} bytes)")
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
# key = the game's internal string ID (same for every player). The patch only contains
# key -> translation: no English game text. Text Translator keeps the translations it receives in
# cache.db; the patch is applied to it by patch_db.CachePatch.
class Patch:
    """Two sections, both key -> translation:

    strings  reviewed translations: always used, replace whatever the addon's database has, and
             are never removed by a glossary change.
    auto     machine translations made centrally, shipped so new users do not wait for them:
             never overwrite a text changed locally, and stay removable by a glossary change.
             A row removed that way is not filled again until a new patch version (built with
             the new glossary) arrives.
    drop     keys that were in 'auto' in an older version and are no longer published: their old
             text is removed once from the addon's database, so the addon asks again (and the
             glossary applies).
    """

    def __init__(self, skip_path: Path | None = None) -> None:
        self.strings: dict[int, str] = {}
        self.auto: dict[int, str] = {}
        self.drop: set[int] = set()
        self.version = 0
        self.glossary_fp = ""  # fingerprint of the glossary option 6 used for 'auto' ("" = unknown)
        self.glossary: Glossary | None = None  # the player's glossary (set by the module)
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

    @staticmethod
    def parse_glossary(data: dict) -> str:
        fp = data.get("glossary", "")
        if not isinstance(fp, str):
            raise ValueError("patch: 'glossary' must be a string")
        return fp

    def load_dict(self, data: dict) -> None:
        version, strings, auto = self.parse(data)
        drop = self.parse_drop(data) - set(strings) - set(auto)
        fp = self.parse_glossary(data)
        with self.lock:
            self.version, self.strings, self.auto, self.drop = version, strings, auto, drop
            self.glossary_fp = fp

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
            if self.glossary_fp:
                data["glossary"] = self.glossary_fp
            return data

    def keys(self) -> set[int]:
        """Keys protected from glossary purges: reviewed ones, and automatic ones when the patch
        was made with the player's glossary."""
        with self.lock:
            keys = set(self.strings)
            auto = set(self.auto)
        return keys | auto if self.auto_current() else keys

    # -- validity of the automatic translations --------------------------------------------
    # Priority: reviewed (strings) > automatic (auto) > translation of the local server.
    # An automatic translation is valid when the patch was made with the player's glossary (same
    # fingerprint). If not, the keys purged by a glossary change (skip file) are left to the local
    # server until a patch made with the new glossary arrives; all other keys stay valid.
    def auto_current(self) -> bool:
        """True if 'auto' was made with the player's glossary: every automatic row is valid."""
        g = self.glossary
        return bool(self.glossary_fp) and g is not None and self.glossary_fp == g.fingerprint

    def _skipped(self) -> set[int]:
        if not self.skip_path or self.auto_current():
            return set()
        try:
            data = json.loads(self.skip_path.read_text(encoding="utf-8"))
            keys = {int(k) for k in data.get("keys", [])}
            if self.glossary_fp:
                return keys  # valid until a patch made with the player's glossary arrives
            if int(data.get("version", -1)) == self.version:
                return keys  # patch without fingerprint (older option 6): old rule
        except Exception:  # noqa: BLE001 - missing or damaged: nothing skipped
            pass
        return set()

    def skipped_state(self) -> tuple:
        """Changes whenever the result of _skipped() may change (patch or glossary updated)."""
        g = self.glossary
        return self.version, self.glossary_fp, g.fingerprint if g is not None else ""

    def suppress(self, keys: set[int]) -> None:
        """Remember auto keys just purged from the addon's database, so they are not filled again."""
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
        """Automatic texts already written into the addon's database (key -> text)."""
        path = self._applied_path()
        try:
            return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        except Exception:  # noqa: BLE001 - missing or damaged: nothing known
            return {}

    def _dropped_path(self) -> Path | None:
        return self.skip_path.with_name("patch_it.dropped.json") if self.skip_path else None

    def _read_dropped(self) -> set[int]:
        """'drop' keys already removed from the addon's database once (never removed twice)."""
        path = self._dropped_path()
        try:
            return {int(k) for k in json.loads(path.read_text(encoding="utf-8"))}
        except Exception:  # noqa: BLE001 - missing or damaged: nothing removed yet
            return set()

    def apply(self, db: Path | None) -> int:
        """Apply the patch to the addon's database; implemented by patch_db.CachePatch."""
        raise NotImplementedError


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
# Patch split in pieces (published by tools/pubblica_pezzi.py, read by the module)
# --------------------------------------------------------------------------- #
# patch/<lang>/index.json   {"format": 1, "version": n, "glossary": "<fingerprint>", "width": 20000,
#                            "pieces": {"p0000.json": {"sha256": ..., "entries": n, "size": bytes}}}
# patch/<lang>/pNNNN.json   the keys from NNNN*width to (NNNN+1)*width-1:
#                           {"strings": {...}, "auto": {...}, "drop": [...], "h": {key: "1a2b3c4d"}}
# A key always stays in the same piece, so a new version only changes the pieces of the keys that
# changed. Version and glossary fingerprint are only in the index: otherwise every piece would
# change at every version. Empty sections are left out.
# "h" = raw_hash() of the raw English text (as Text Translator sends it) the translation was made
# from: the module does not use a translation whose English text changed. It is a fingerprint, not
# the text: no English game text is published.
PATCH_FORMAT = 1
PIECE_WIDTH = 20_000              # default width of a piece (keys); the index says the real one
PIECE_MAX_BYTES = 5_000_000       # option 8 warns above this: make the width smaller
PIECE_RE = re.compile(r"p(\d{4,7})\.json")
HASH_RE = re.compile(r"[0-9a-f]{8}")


def raw_hash(english: str) -> str:
    """Fingerprint of an English text: first 8 hex digits of the SHA-256 of its UTF-8 bytes."""
    return hashlib.sha256(english.encode("utf-8")).hexdigest()[:8]


def piece_name(number: int) -> str:
    return f"p{number:04d}.json"


def piece_number(name: str) -> int | None:
    m = PIECE_RE.fullmatch(name)
    return int(m.group(1)) if m else None


def _ordered(section: dict[int, str]) -> dict[str, str]:
    return {str(k): section[k] for k in sorted(section)}


def piece_bytes(strings: dict[int, str], auto: dict[int, str], drop: set[int],
                hashes: dict[int, str]) -> bytes:
    """Content of one piece: always the same bytes for the same entries (sorted, compact)."""
    data: dict = {}
    if strings:
        data["strings"] = _ordered(strings)
    if auto:
        data["auto"] = _ordered(auto)
    if drop:
        data["drop"] = sorted(drop)
    if hashes:
        data["h"] = _ordered(hashes)
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def split_patch(strings: dict[int, str], auto: dict[int, str], drop: set[int],
                hashes: dict[int, str], width: int) -> dict[str, bytes]:
    """Piece file name -> content. `hashes` may hold keys that are not published (ignored)."""
    if width <= 0:
        raise ValueError("width must be positive")
    groups: dict[int, tuple[dict, dict, set, dict]] = {}

    def group(k: int) -> tuple[dict, dict, set, dict]:
        return groups.setdefault(k // width, ({}, {}, set(), {}))

    for k, v in strings.items():
        group(k)[0][k] = v
    for k, v in auto.items():
        if k not in strings:
            group(k)[1][k] = v
    for k in drop:
        if k not in strings and k not in auto:
            group(k)[2].add(k)
    for k, h in hashes.items():
        if (k in strings or k in auto) and h:
            group(k)[3][k] = h
    return {piece_name(n): piece_bytes(*groups[n]) for n in sorted(groups)}


def index_bytes(version: int, glossary: str, width: int, pieces: dict[str, bytes]) -> bytes:
    info = {}
    for name in sorted(pieces):
        body = pieces[name]
        data = json.loads(body)
        entries = sum(len(data.get(s, ())) for s in ("strings", "auto", "drop"))
        info[name] = {"sha256": hashlib.sha256(body).hexdigest(), "entries": entries,
                      "size": len(body)}
    data = {"format": PATCH_FORMAT, "version": version, "glossary": glossary, "width": width,
            "pieces": info}
    return (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def parse_index(data: dict) -> dict:
    """Checks an index.json; returns it with "pieces" as {name: (number, sha256, size)}."""
    if not isinstance(data, dict) or data.get("format") != PATCH_FORMAT:
        raise ValueError("patch index: unknown format")
    version, width = data.get("version"), data.get("width")
    if not isinstance(version, int) or version < 0:
        raise ValueError("patch index: bad version")
    if not isinstance(width, int) or width <= 0:
        raise ValueError("patch index: bad width")
    fp = data.get("glossary", "")
    if not isinstance(fp, str):
        raise ValueError("patch index: 'glossary' must be a string")
    pieces = data.get("pieces")
    if not isinstance(pieces, dict):
        raise ValueError("patch index: 'pieces' must be an object")
    out = {}
    for name, info in pieces.items():
        number = piece_number(str(name))
        if number is None or not isinstance(info, dict):
            raise ValueError(f"patch index: bad piece {name!r}")
        sha, size = info.get("sha256"), info.get("size")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"patch index: bad sha256 for {name}")
        if not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD:
            raise ValueError(f"patch index: bad size for {name}")
        out[name] = (number, sha, size)
    return {"version": version, "glossary": fp, "width": width, "pieces": out}


def parse_piece(data: dict, number: int, width: int
                ) -> tuple[dict[int, str], dict[int, str], set[int], dict[int, str]]:
    """Checks a piece with the same rules as Patch.load_dict; every key must belong to it."""
    if not isinstance(data, dict):
        raise ValueError("patch piece: not an object")
    strings = Patch._section(data, "strings", False)
    auto = {k: v for k, v in Patch._section(data, "auto", False).items() if k not in strings}
    drop = Patch.parse_drop(data) - set(strings) - set(auto)
    raw = data.get("h", {})
    if not isinstance(raw, dict):
        raise ValueError("patch piece: 'h' must be an object")
    hashes = {}
    for k, h in raw.items():
        if not str(k).isdigit() or not isinstance(h, str) or not HASH_RE.fullmatch(h):
            raise ValueError(f"patch piece: bad 'h' entry {k!r}")
        if int(k) in strings or int(k) in auto:
            hashes[int(k)] = h
    low, high = number * width, (number + 1) * width
    for k in (*strings, *auto, *drop):
        if not low <= k < high:
            raise ValueError(f"patch piece {piece_name(number)}: key {k} outside {low}-{high - 1}")
    return strings, auto, drop, hashes


# --------------------------------------------------------------------------- #
# Local map: string ID <-> English <-> translation (private, never shared)
# --------------------------------------------------------------------------- #
def norm(text: str) -> str:
    return html.unescape(text).strip()


class KeyMap:
    """Map string ID -> English -> translation (map_<lang>.db), filled by the module with every text it
    answers. It stays on this PC: it contains English game text."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.closed = False
        self.con = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        # WAL + synchronous=NORMAL: each save is appended to map_<lang>.db-wal without forcing a
        # write to disk every time (before: one forced disk write for every answer).
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("""CREATE TABLE IF NOT EXISTS texts (
            key INTEGER PRIMARY KEY NOT NULL,
            en TEXT NOT NULL,
            it TEXT,
            how TEXT,          -- 'live' (seen while playing), 'contrib...' (merged from a contributor)
            seen INTEGER)""")
        self.con.commit()
        # The addon ends the module without closing the connection, so close() does not run when
        # the game closes. Merge now what the previous session left in the -wal, so map_<lang>.db
        # alone is complete (e.g. when a contributor sends it). A failure here is harmless.
        try:
            busy, _, _ = self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy:
                log.debug("map: checkpoint at start not completed (file in use)")
        except sqlite3.Error as exc:
            log.debug("map: checkpoint at start failed: %s", exc)
        self.lock = threading.Lock()
        # Since close() does not run when the game closes, the rows of the current session would
        # stay in the -wal until the next start. A background thread merges them into
        # map_<lang>.db every CHECKPOINT_EVERY seconds, so map_<lang>.db alone is at most that
        # far behind, also after a crash. (The thread ends with the module.)
        self.dirty = False
        self.stop = threading.Event()
        threading.Thread(target=self._checkpoint_loop, daemon=True, name="map-checkpoint").start()

    CHECKPOINT_EVERY = 120  # seconds

    def _checkpoint_loop(self) -> None:
        while not self.stop.wait(self.CHECKPOINT_EVERY):
            with self.lock:
                if self.closed:
                    return
                if not self.dirty:
                    continue
                try:
                    # PASSIVE: never waits for other readers (e.g. the management page), so a save
                    # is never blocked; what it cannot copy now is copied at the next round.
                    _, wal, done = self.con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                    if wal == done:
                        self.dirty = False
                    else:
                        log.debug("map: checkpoint partial (%s of %s pages, file in use)", done, wal)
                except sqlite3.Error as exc:
                    log.debug("map: checkpoint failed: %s", exc)

    def save(self, rows: list[tuple[int, str, str, str]]) -> int:
        """rows: (key, en, it, how). A 'live' row always wins over an older kind."""
        if not rows:
            return 0
        now = int(time.time())
        with self.lock:
            if self.closed:
                return 0
            with self.con:
                before = self.con.total_changes
                self.con.executemany(
                    "INSERT INTO texts (key, en, it, how, seen) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET en = excluded.en, it = excluded.it, "
                    "how = excluded.how, seen = excluded.seen "
                    "WHERE excluded.how = 'live' OR texts.how = 'backfill'",
                    [(k, en, it, how, now) for k, en, it, how in rows])
                changed = self.con.total_changes - before
            if changed:
                self.dirty = True
            return changed

    def count(self) -> int:
        with self.lock:
            return self.con.execute("SELECT count(*) FROM texts").fetchone()[0]

    def close(self) -> None:
        """Write the -wal file into map_<lang>.db and go back to a single file, then close.
        Called when the addon closes the connection; the addon usually ends the module instead,
        so the same merge is also done at the next start (__init__)."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.stop.set()
            try:
                self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                try:
                    self.con.execute("PRAGMA journal_mode=DELETE")
                except sqlite3.Error:
                    pass  # another tool has the file open: it stays in WAL mode, no harm
            finally:
                self.con.close()


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


class Engine:
    """OPUS-MT translation with the glossary, line by line.

    A single background worker translates the queued lines and stores them in a cache that is
    also saved on disk (cache_it.jsonl). The module (lt_module.Dispatcher) puts lines in the
    queue and reads the results from the cache.
    """

    BATCH = 24
    MAX_FAILURES = 2  # failed batches before a line is left in English (until restart)

    def __init__(self, translator, glossary: Glossary, cache_path: Path | None = None,
                 addon_db: Path | None = None, patch: "Patch | None" = None) -> None:
        self.translator, self.glossary = translator, glossary  # translator may be None until loaded
        self.cache: dict[str, str] = {}
        self.queue: deque[str] = deque()
        self.queued: set[str] = set()
        self.cv = threading.Condition()
        self.cache_path = cache_path
        self.addon_db = addon_db  # the addon's translation database (the module passes cache.db)
        # glossary terms the cache was built with, to invalidate only what changes
        self.snapshot_path = cache_path.with_suffix(".glossary.json") if cache_path else None
        self.gen = 0  # bumped at every glossary change; batches started before it are discarded
        self.done_count = 0
        self.patch = patch                 # its reviewed rows are never purged
        # Lines of every finished batch (translated or failed), read by lt_module.Dispatcher so it
        # checks only the requests waiting for those lines. None = nobody reads it (not filled).
        self.finished: list[str] | None = None
        self.failures: dict[str, int] = {}  # line -> failed batches so far
        self.given_up: set[str] = set()     # failed MAX_FAILURES times: English until restart
        # Seconds spent so far preparing lines (glossary protection) and inside the model; only the
        # worker writes them, the module reads them for its periodic "stats" log line.
        self.time_prepare = 0.0
        self.time_model = 0.0
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
                {str(k): str(v) for k, v in data["exact"].items()},
                "protect": int(data.get("protect", 1))}

    def outdated(self, line: str, version: int) -> bool:
        """True if a translation made with protection rules `version` must be redone."""
        return ((version < 5 and plural_name(line))
                or (version < 3 and PLURAL_RE.search(line) is not None)
                or (version < 2 and self._glued_term(line)))

    def _glued_term(self, line: str) -> bool:
        """True if the line has a glossary term glued to markup, that the protection rules before
        PROTECT_VERSION 2 left to the model (e.g. "Large Bone[s]" -> "Grande Bone[s]")."""
        if not TOKEN_RE.search(line):
            return False
        protected = TOKEN_RE.sub("QZ0QZ", line)
        g = self.glossary
        for src, _ in g.terms:
            if src in protected and g.term_regex(src).search(protected) \
                    and not g.old_term_regex(src).search(protected):
                return True
        return False

    def sync_glossary(self) -> None:
        """Called at startup and when the glossary changes.

        Compares the glossary with the one the cache was built with and drops only the cached
        lines that contain a term that was added, removed or changed; everything else is kept.
        The old translations of those lines are also removed from the addon's own database
        (_purge_addon_db), otherwise the addon would keep showing them and never ask again.
        """
        new_terms = dict(self.glossary.terms)
        new_exact = dict(self.glossary.exact)
        purge: set[str] = set()  # old translated texts to remove from the addon database
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
                if old["protect"] < PROTECT_VERSION and self.cache:
                    # translated with older protection rules: translate these lines again
                    redo = [s for s, t in self.cache.items() if self.outdated(s, old["protect"])
                            or (old["protect"] < 4 and bad_percent(t, s))]
                    for s in redo:
                        purge.add(self.cache.pop(s))
                    dropped += len(redo)
                    if redo:
                        log.info("glossary sync: %d cached lines made with older protection "
                                 "rules dropped (translated again)", len(redo))
            upgrade = old is not None and old["protect"] < PROTECT_VERSION
            try:
                if dropped or purge or (changed is None and self.cache_path
                                        and self.cache_path.exists()):
                    self._rewrite_cache()
                if self.snapshot_path and (changed is None or changed or upgrade
                                           or old["exact"] is None):
                    tmp = self.snapshot_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"terms": new_terms, "exact": new_exact,
                                               "protect": PROTECT_VERSION},
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
        """Remove old translations containing these texts from the addon's database.
        Implemented by lt_module.ModuleEngine; without an addon database there is nothing to do."""


    # -- lines -------------------------------------------------------------------------------
    @staticmethod
    def _needs_translation(line: str) -> bool:
        if GARBLED_RE.search(line):
            return False  # undecoded game data: shown as it is
        return re.search(r"[A-Za-z]{2}", TOKEN_RE.sub("", line)) is not None

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
                results = None
            with self.cv:
                if gen != self.gen:
                    # glossary changed while translating: redo this batch with the new terms
                    self.queue.extendleft(reversed(batch))
                    self.cv.notify_all()
                    continue
                if results is None:
                    # failed: tried again when asked again; after MAX_FAILURES the line stays in
                    # English until the next start (never saved in the cache)
                    results = {}
                    gave_up = 0
                    for line in batch:
                        n = self.failures.get(line, 0) + 1
                        if n >= self.MAX_FAILURES:
                            self.failures.pop(line, None)
                            self.given_up.add(line)
                            gave_up += 1
                        else:
                            self.failures[line] = n
                    if gave_up:
                        log.warning("%d lines failed %d times: left in English until restart",
                                    gave_up, self.MAX_FAILURES)
                else:
                    for line in results:
                        self.failures.pop(line, None)
                self.cache.update(results)
                for line in batch:
                    self.queued.discard(line)
                if self.finished is not None:
                    self.finished.extend(batch)
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
        t0 = time.perf_counter()
        # names with plural markers: singular (plural markers added below); the rest: plural form
        srcs = [english_singular(line) if plural_name(line) else resolve_plural(line)
                for line in lines]
        prepared = [self._protect(src) for src in srcs]
        # lines made only of markup and glossary terms ("Large Bone[s]") need no model
        model_idx = [i for i, (p, _) in enumerate(prepared)
                     if re.search(r"[A-Za-z]", re.sub(r"QZ\d+QZ", "", p))]
        t1 = time.perf_counter()
        self.time_prepare += t1 - t0
        try:
            model_outs = self.translator.translate([prepared[i][0] for i in model_idx]) if model_idx else []
        finally:
            self.time_model += time.perf_counter() - t1
        outs = [p for p, _ in prepared]
        for i, out in zip(model_idx, model_outs):
            outs[i] = out
        for line, src, (_, restore), out in zip(lines, srcs, prepared, outs):
            if not out.strip() or not all(ph in out for ph in restore):
                results[line] = line  # a placeholder got lost: keep the English text, never break markup
                continue
            for ph, original in restore.items():
                out = out.replace(ph, original)
            out = fix_percent(out, line)
            results[line] = italian_plural(line, src, out) if plural_name(line) else out
        return results


# --------------------------------------------------------------------------- #
# Developer commands: review the map in a spreadsheet, build the patch
# --------------------------------------------------------------------------- #
REVIEW_COLUMNS = ["key", "inglese", "italiano_attuale", "italiano_patch", "nuova_traduzione"]


def export_review(map_path: Path, patch_path: Path, out: Path) -> int:
    """CSV for Excel/LibreOffice (';' separated, UTF-8). Fill 'nuova_traduzione' and import it.

    One row for every text in the map or in the patch; 'italiano_attuale' is what players will
    see once the patch is published. Any text seen
    in game can be found by its translated text; 'inglese' is empty when the map does not know it.
    Contains English game text: keep it on your PC, never publish it.
    """
    import csv
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
    # the patch you are about to publish wins over the map: after option 6 the new automatic
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


def dump_patch(data: dict) -> str:
    """Text of patch_it.json: compact (no spaces) but one entry per line, so it stays small for
    the modules up to v0.4 that download it whole, and git diffs stay readable."""
    return json.dumps(data, ensure_ascii=False, indent=0, separators=(",", ":")) + "\n"


def _write_patch(patch: Patch, path: Path) -> None:
    data = patch.to_dict()
    for section in ("strings", "auto"):  # last check on the whole patch: lone "%" -> "%%"
        data[section] = {k: fix_percent(v) for k, v in data[section].items()}
    Patch.parse(data)  # never write an invalid file
    Patch.parse_glossary(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump_patch(data), encoding="utf-8")
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

    translations     line -> translation already available (cache); missing lines are translated
    translate_lines  function(list of lines) -> {line: translation}; it also saves its own cache,
                     so an interrupted run continues where it stopped
    Rows that would stay identical to the English text are skipped: they add nothing and would
    publish English game text. Strings without English in the map keep their old entry.

    Returns (rows in auto, rows skipped, lines translated now).
    """
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
        return fixed if fixed is not None else fix_percent(translations.get(line, line), line)

    auto = {k: v for k, v in patch.auto.items() if k not in english and k not in patch.strings}
    skipped = 0
    for key, en in english.items():
        lines = en.split("\n")
        outs = [result(line) for line in lines]
        if GARBLED_RE.search(en) or not "\n".join(outs).strip():
            skipped += 1  # undecoded game data or an empty result: never published
            continue
        if "\n".join(outs).strip() in (en.strip(), resolve_plural(en).strip()) or any(
                o.strip() in (e.strip(), resolve_plural(e).strip()) and Engine._needs_translation(e)
                for e, o in zip(lines, outs)):
            skipped += 1  # still (partly) in English: names kept as they are, failed lines
            continue
        auto[key] = "\n".join(outs)
    # keys leaving 'auto' go into 'drop': players still holding their old text get it removed
    drop = (patch.drop | (set(patch.auto) - set(auto))) - set(auto) - set(patch.strings)
    if auto != patch.auto or drop != patch.drop or patch.glossary_fp != g.fingerprint:
        patch.auto = auto
        patch.drop = drop
        patch.glossary_fp = g.fingerprint  # the module checks it against the player's glossary
        patch.version += 1
        _write_patch(patch, patch_path)
    return len(auto), skipped, done

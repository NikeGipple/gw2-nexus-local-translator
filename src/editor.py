"""Local Translator: a page to correct translations while playing (http://127.0.0.1:8770).

Search a text as seen in the game (Italian, English when the module knows it, or the string ID),
change it and save: the translation is written into
- patch_it.db, as a reviewed row: the module writes it into cache.db and sends it to the game at
  once (Text Translator shows an answer it did not ask for);
- map_it.db, with how = 'user input', so the translations written by hand can be told apart
  (the map is what helpers send to the author); they are written again over every patch update.

Before every save, the state of the key (map row, patch row, text shown in game) goes into
history_it.db, next to patch_it.db: "Annulla" puts back the last one, so a wrong save can be undone
step by step, back to the automatic or reviewed translation it replaced.

The server listens on 127.0.0.1 only, accepts only requests addressed to that host (no DNS
rebinding) and saves only same-origin JSON requests (no other site can post to it).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import lt_server as lt
from patch_db import KIND_AUTO, KIND_DROP, KIND_REVIEWED, CachePatch, untagged

log = logging.getLogger("lt")

EDITOR_PORT = 8770
RESULTS = 50          # rows per search
MAX_BYTES = 65535     # longest text the addon accepts (UTF-8 bytes)
KINDS = {KIND_REVIEWED: "reviewed", KIND_AUTO: "automatic", KIND_DROP: "drop"}

# markup the game does not show: plural/feminine forms, colors, line breaks, escaped brackets
MARKUP = [(re.compile(r'\[(?:pl|f):"[^"]*"\]'), ""), (re.compile(r"</?c(?:=[^>]*)?/?>"), ""),
          (re.compile(r"<br>"), " "), (re.compile(r"\[lbracket\]"), "["),
          (re.compile(r"\[rbracket\]"), "]"), (re.compile(r"\s+"), " ")]


def plain(text: str) -> str:
    """A text as the game shows it, more or less: for searching."""
    for pattern, repl in MARKUP:
        text = pattern.sub(repl, text)
    return text.strip()


HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY,
    key         INTEGER NOT NULL,
    at          INTEGER NOT NULL,
    map_row     TEXT,      -- JSON [en, it, how] of map_it.db before the save, NULL: no row
    patch_row   TEXT,      -- JSON [kind, text, src_hash, piece] of patch_it.db, NULL: no row
    shown       TEXT,      -- text of cache.db (what the game showed), NULL: none
    shown_hash  INTEGER    -- its src_hash
);
CREATE INDEX IF NOT EXISTS history_key ON history(key, id);
"""


class Editor:
    def __init__(self, patch: CachePatch, keymap: lt.KeyMap, cache_db: Path, cache_key: str) -> None:
        self.patch, self.keymap, self.cache_db, self.cache_key = patch, keymap, cache_db, cache_key
        self.lock = threading.Lock()  # one save or rollback at a time
        self.history = sqlite3.connect(str(patch.path.with_name("history_it.db")), timeout=10,
                                       check_same_thread=False)
        self.history.executescript(HISTORY_SCHEMA)

    def _cache(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.cache_db}?mode=ro", uri=True, timeout=10)

    def search(self, q: str, exact: bool = False) -> list[dict]:
        """Texts containing `q` (Italian as in cache.db, or English); with `exact`, only the texts
        that are `q` and nothing else (same letters and case). Digits only: that string ID."""
        q = q.strip()
        if not q:
            return []
        keys: list[int] = []
        if q.isdigit():
            keys = [int(q)]
        else:
            if self.cache_db.is_file():
                # compared as the game shows them: without markup and "show keys" prefix
                if exact:
                    def match(t: str) -> bool:
                        return t == q
                else:
                    folded = q.casefold()

                    def match(t: str) -> bool:
                        return folded in t.casefold()
                con = self._cache()
                try:
                    for k, t in con.execute("SELECT id, text FROM translations WHERE cache_key = ? "
                                            "AND text IS NOT NULL", (self.cache_key,)):
                        if match(plain(untagged(k, t))):
                            keys.append(k)
                            if len(keys) >= RESULTS:
                                break
                finally:
                    con.close()
            keys += [k for k in self.keymap.search_english(q, RESULTS, exact) if k not in keys]
        return [self.row(k) for k in keys[:RESULTS]]

    def _shown(self, key: int) -> tuple[str | None, int | None]:
        """(text, src_hash) of the key in cache.db, as stored (with its "show keys" prefix)."""
        if not self.cache_db.is_file():
            return None, None
        con = self._cache()
        try:
            r = con.execute("SELECT text, src_hash FROM translations WHERE cache_key = ? AND id = ?",
                            (self.cache_key, key)).fetchone()
        finally:
            con.close()
        return (r[0], r[1]) if r else (None, None)

    def row(self, key: int) -> dict:
        text, _h = self._shown(key)
        shown = untagged(key, text) if text is not None else None
        entry = self.patch.entry(key)
        map_row = self.keymap.get(key)
        undo = self.history.execute("SELECT count(*) FROM history WHERE key = ?", (key,)).fetchone()[0]
        return {"key": key, "shown": shown, "en": map_row[0] if map_row else "",
                "kind": KINDS.get(entry[0]) if entry else None,
                "patch": entry[1] if entry else None,
                "user": bool(map_row) and map_row[2] == lt.USER_INPUT, "undo": undo}

    def recent(self) -> list[dict]:
        return [{"key": k, "en": en, "it": it, "seen": seen}
                for k, en, it, seen in self.keymap.recent_user()]

    def save(self, key: int, text: str) -> dict:
        if not text.strip():
            raise ValueError("empty text")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("text too long")
        with self.lock:
            shown, shown_hash = self._shown(key)
            map_row, patch_row = self.keymap.get(key), self.patch.full_entry(key)
            with self.history:
                self.history.execute(
                    "INSERT INTO history (key, at, map_row, patch_row, shown, shown_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (key, int(time.time()), json.dumps(map_row) if map_row else None,
                     json.dumps(patch_row) if patch_row else None, shown, shown_hash))
            self.keymap.save_user(key, text)
            self.patch.set_user(key, text)
            written = self.patch.apply(self.cache_db, {key})
        log.info("editor: key %d translated by hand%s", key,
                 "" if written else " (cache.db not changed: patch off, or same text)")
        return self.row(key)

    def rollback(self, key: int) -> dict:
        """Undo the last save of the key: map row, patch row and the text in the game as before."""
        with self.lock:
            last = self.history.execute(
                "SELECT id, map_row, patch_row, shown, shown_hash FROM history WHERE key = ? "
                "ORDER BY id DESC LIMIT 1", (key,)).fetchone()
            if last is None:
                raise ValueError("nothing to undo")
            hid, map_row, patch_row, shown, shown_hash = last
            self.keymap.restore(key, tuple(json.loads(map_row)) if map_row else None)
            self.patch.restore_entry(key, tuple(json.loads(patch_row)) if patch_row else None)
            sent = self.patch.apply(self.cache_db, {key})
            now, now_hash = self._shown(key)
            if not sent and shown is not None and now != shown:
                # the old text did not come from the patch (model translation): sent as it was
                h = now_hash if now_hash is not None else shown_hash or 0
                sent = self.patch.send_text(key, h, shown)
            with self.history:
                self.history.execute("DELETE FROM history WHERE id = ?", (hid,))
        log.info("editor: key %d rolled back%s", key,
                 "" if sent or shown is None else " (text in game unchanged until next start)")
        return self.row(key)


def _handler(editor: Editor, port: int) -> type[BaseHTTPRequestHandler]:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalTranslator"

        def log_message(self, fmt, *args) -> None:  # noqa: D102 - quiet: the module has its log
            log.debug("editor: " + fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            if self.headers.get("Host") in hosts:
                return True
            self._json({"error": "bad host"}, HTTPStatus.FORBIDDEN)
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/":
                    self._send(HTTPStatus.OK, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif url.path == "/api/search":
                    query = parse_qs(url.query)
                    self._json(editor.search(query.get("q", [""])[0],
                                             query.get("exact", ["0"])[0] == "1"))
                elif url.path == "/api/recent":
                    self._json(editor.recent())
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:  # noqa: BLE001
                log.warning("editor: %s failed: %s", url.path, exc)
                self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            if self.headers.get("Origin") not in origins or not self.headers.get(
                    "Content-Type", "").startswith("application/json"):
                self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            path = urlsplit(self.path).path
            if path not in ("/api/save", "/api/rollback"):
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                size = int(self.headers.get("Content-Length") or 0)
                if size > 4 * MAX_BYTES:
                    raise ValueError("request too big")
                data = json.loads(self.rfile.read(size).decode("utf-8"))
                key = int(data["key"])
                if not 0 <= key < 2 ** 32:
                    raise ValueError("bad key")
                if path == "/api/rollback":
                    self._json(editor.rollback(key))
                else:
                    self._json(editor.save(key, str(data["text"])))
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001
                log.warning("editor: %s failed: %s", path, exc)
                self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return Handler


def start(editor: Editor, port: int = EDITOR_PORT) -> ThreadingHTTPServer | None:
    """Serve the page in a thread; None (logged) if the port is taken."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _handler(editor, port))
    except OSError as exc:
        log.warning("editor page not started (port %d): %s", port, exc)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="editor").start()
    log.info("editor page: http://127.0.0.1:%d", port)
    return server


PAGE = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Correzioni traduzione</title>
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1d2229; --muted: #646d78; --line: #dde1e6;
  --accent: #2f6fdb; --accent-text: #ffffff; --ok: #1e7f4f; --err: #b3261e;
  --badge-rev: #e3f1e8; --badge-auto: #eef0f3; --badge-user: #e6eefc;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15181c; --panel: #1e2227; --text: #e6e9ed; --muted: #9aa3ad; --line: #30363d;
    --accent: #5b8ff0; --accent-text: #0d1117; --ok: #5cc28c; --err: #f08b84;
    --badge-rev: #1f3a2b; --badge-auto: #2a2f36; --badge-user: #1f2f4d;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 15px/1.45 system-ui, "Segoe UI", sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 20px 16px 40px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 16px; font-size: 13px; }
form.search { display: flex; gap: 8px; margin-bottom: 8px; }
label.opt { display: block; margin: 0 0 16px; font-size: 14px; cursor: pointer; }
label.opt input { margin: 0 6px 0 0; vertical-align: -2px; }
.hint { color: var(--muted); font-size: 12px; }
input[type=search] { flex: 1; min-width: 0; padding: 9px 12px; font: inherit; color: var(--text);
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
button { padding: 8px 14px; font: inherit; border-radius: 8px; border: 1px solid var(--accent);
  background: var(--accent); color: var(--accent-text); cursor: pointer; }
button.ghost { background: transparent; color: var(--accent); }
button:disabled { opacity: .5; cursor: default; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
        padding: 12px 14px; margin-bottom: 10px; }
.head { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin-bottom: 6px;
        font-size: 13px; color: var(--muted); }
.key { font-family: ui-monospace, Consolas, monospace; color: var(--text); }
.badge { padding: 1px 8px; border-radius: 999px; font-size: 12px; color: var(--text); }
.b-reviewed { background: var(--badge-rev); } .b-automatic, .b-none { background: var(--badge-auto); }
.b-user { background: var(--badge-user); }
.en { color: var(--muted); font-size: 13px; margin: 0 0 6px; white-space: pre-wrap; }
textarea { width: 100%; min-height: 64px; padding: 8px 10px; font: inherit; color: var(--text);
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px; resize: vertical; }
.actions { display: flex; gap: 10px; align-items: center; margin-top: 8px; }
.status { font-size: 13px; } .status.ok { color: var(--ok); } .status.err { color: var(--err); }
.empty { color: var(--muted); padding: 8px 2px; }
h2 { font-size: 15px; margin: 28px 0 8px; }
.recent .card { padding: 8px 12px; cursor: pointer; }
.recent .it { white-space: pre-wrap; }
</style>
</head>
<body>
<main>
  <h1>Correzioni traduzione</h1>
  <p class="sub">Cerca il testo come lo vedi in gioco (o in inglese, o l'ID della stringa), correggi e salva: in gioco cambia in un paio di secondi.</p>
  <form class="search" id="f">
    <input type="search" id="q" placeholder="Es. Utilizza l'attacco, oppure 37952" autofocus>
    <button type="submit">Cerca</button>
  </form>
  <label class="opt"><input type="checkbox" id="exact"> Cerca la stringa esatta
    <span class="hint">(solo i testi che sono esattamente questo, maiuscole comprese)</span></label>
  <div id="results"></div>
  <h2>Ultime correzioni</h2>
  <div class="recent" id="recent"></div>
</main>
<script>
const $ = (s) => document.querySelector(s);
const KIND = { reviewed: "revisionata", automatic: "automatica", drop: "rimossa" };

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e[k] = v; else e.setAttribute(k, v);
  }
  for (const c of kids) if (c != null) e.append(c);
  return e;
}

function card(r, note) {
  const ta = el("textarea", {});
  ta.value = r.patch ?? r.shown ?? "";
  const status = el("span", { class: "status" + (note ? " ok" : "") }, note || "");
  const btn = el("button", { type: "button" }, "Salva");
  const undo = el("button", { type: "button", class: "ghost",
    title: r.undo ? "Torna al testo precedente (" + r.undo + " passi disponibili)" : "Nessun salvataggio da annullare" }, "Annulla");
  undo.disabled = !r.undo;
  const self = el("div", { class: "card" });
  const post = async (url, body, busy, done) => {
    btn.disabled = undo.disabled = true; status.className = "status"; status.textContent = busy;
    try {
      const res = await fetch(url, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);
      self.replaceWith(card(data, done));
      loadRecent();
    } catch (e) {
      status.className = "status err"; status.textContent = "Errore: " + e.message;
      btn.disabled = false; undo.disabled = !r.undo;
    }
  };
  btn.onclick = () => post("/api/save", { key: r.key, text: ta.value }, "Salvataggio…", "Salvato, inviato al gioco");
  undo.onclick = () => post("/api/rollback", { key: r.key }, "Annullamento…", "Ripristinato il testo precedente");
  const badges = [];
  badges.push(el("span", { class: "badge b-" + (r.kind || "none") }, r.kind ? KIND[r.kind] : "non nella patch"));
  if (r.user) badges.push(el("span", { class: "badge b-user" }, "user input"));
  self.append(
    el("div", { class: "head" }, el("span", { class: "key" }, "#" + r.key), ...badges),
    r.en ? el("p", { class: "en" }, r.en) : null,
    ta,
    el("div", { class: "actions" }, btn, undo, status));
  return self;
}

async function search(q) {
  const box = $("#results");
  box.replaceChildren(el("p", { class: "empty" }, "Ricerca…"));
  try {
    const res = await fetch("/api/search?q=" + encodeURIComponent(q) + ($("#exact").checked ? "&exact=1" : ""));
    const rows = await res.json();
    if (!res.ok) throw new Error(rows.error || res.status);
    box.replaceChildren(...(rows.length ? rows.map((r) => card(r)) : [el("p", { class: "empty" }, "Nessun risultato.")]));
  } catch (e) {
    box.replaceChildren(el("p", { class: "status err" }, "Errore: " + e.message));
  }
}

async function loadRecent() {
  try {
    const rows = await (await fetch("/api/recent")).json();
    $("#recent").replaceChildren(...(rows.length ? rows.map((r) =>
      el("div", { class: "card", onclick: () => { $("#q").value = r.key; search(String(r.key)); scrollTo(0, 0); } },
        el("div", { class: "head" }, el("span", { class: "key" }, "#" + r.key),
          el("span", {}, new Date(r.seen * 1000).toLocaleString("it-IT"))),
        el("div", { class: "it" }, r.it))) : [el("p", { class: "empty" }, "Nessuna correzione ancora.")]));
  } catch (e) { /* page still usable */ }
}

$("#f").onsubmit = (ev) => { ev.preventDefault(); const q = $("#q").value.trim(); if (q) search(q); };
$("#exact").onchange = () => { const q = $("#q").value.trim(); if (q) search(q); };
loadRecent();
</script>
</body>
</html>
"""

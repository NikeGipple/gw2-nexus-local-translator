"""Local Translator: a page to correct translations while playing (http://127.0.0.1:8770).

Search a text as seen in the game (Italian, English when the module knows it, or the string ID),
change it and save: the translation is written into
- patch_it.db, as a reviewed row: the module writes it into cache.db and sends it to the game at
  once (Text Translator shows an answer it did not ask for);
- map_it.db, with how = 'user input', so the translations written by hand can be told apart
  (the map is what helpers send to the author); they are written again over every patch update.

The server listens on 127.0.0.1 only, accepts only requests addressed to that host (no DNS
rebinding) and saves only same-origin JSON requests (no other site can post to it).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
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


class Editor:
    def __init__(self, patch: CachePatch, keymap: lt.KeyMap, cache_db: Path, cache_key: str) -> None:
        self.patch, self.keymap, self.cache_db, self.cache_key = patch, keymap, cache_db, cache_key
        self.lock = threading.Lock()  # one save at a time

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
                con = self._cache()
                try:
                    if exact:  # also with the "<key> - " prefix of the "show keys" mode
                        sql = "(text = ? OR text = id || ' - ' || ?)"
                        args = (q, q)
                    else:
                        sql = "text LIKE ? ESCAPE '\\'"
                        args = (lt.like_pattern(q),)
                    keys = [k for (k,) in con.execute(
                        f"SELECT id FROM translations WHERE cache_key = ? AND {sql} LIMIT ?",
                        (self.cache_key, *args, RESULTS))]
                finally:
                    con.close()
            keys += [k for k in self.keymap.search_english(q, RESULTS, exact) if k not in keys]
        return [self.row(k) for k in keys[:RESULTS]]

    def row(self, key: int) -> dict:
        shown = None
        if self.cache_db.is_file():
            con = self._cache()
            try:
                r = con.execute("SELECT text FROM translations WHERE cache_key = ? AND id = ?",
                                (self.cache_key, key)).fetchone()
            finally:
                con.close()
            shown = untagged(key, r[0]) if r and r[0] is not None else None
        entry = self.patch.entry(key)
        user = self.keymap.user_rows().get(key)
        return {"key": key, "shown": shown, "en": self.keymap.english(key),
                "kind": KINDS.get(entry[0]) if entry else None,
                "patch": entry[1] if entry else None, "user": user is not None}

    def recent(self) -> list[dict]:
        return [{"key": k, "en": en, "it": it, "seen": seen}
                for k, en, it, seen in self.keymap.recent_user()]

    def save(self, key: int, text: str) -> dict:
        if not text.strip():
            raise ValueError("empty text")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("text too long")
        with self.lock:
            english = self.keymap.save_user(key, text)
            self.patch.set_user(key, text, english)
            written = self.patch.apply(self.cache_db, {key})
        log.info("editor: key %d translated by hand%s", key,
                 "" if written else " (cache.db not changed: patch off, or same text)")
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
            if urlsplit(self.path).path != "/api/save":
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                size = int(self.headers.get("Content-Length") or 0)
                if size > 4 * MAX_BYTES:
                    raise ValueError("request too big")
                data = json.loads(self.rfile.read(size).decode("utf-8"))
                key, text = int(data["key"]), str(data["text"])
                if not 0 <= key < 2 ** 32:
                    raise ValueError("bad key")
                self._json(editor.save(key, text))
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001
                log.warning("editor: save failed: %s", exc)
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

function card(r) {
  const ta = el("textarea", {});
  ta.value = r.patch ?? r.shown ?? "";
  const status = el("span", { class: "status" });
  const btn = el("button", { type: "button" }, "Salva");
  btn.onclick = async () => {
    btn.disabled = true; status.className = "status"; status.textContent = "Salvataggio…";
    try {
      const res = await fetch("/api/save", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: r.key, text: ta.value }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.status);
      status.className = "status ok"; status.textContent = "Salvato, inviato al gioco";
      loadRecent();
    } catch (e) {
      status.className = "status err"; status.textContent = "Errore: " + e.message;
    } finally { btn.disabled = false; }
  };
  const badges = [];
  badges.push(el("span", { class: "badge b-" + (r.kind || "none") }, r.kind ? KIND[r.kind] : "non nella patch"));
  if (r.user) badges.push(el("span", { class: "badge b-user" }, "user input"));
  return el("div", { class: "card" },
    el("div", { class: "head" }, el("span", { class: "key" }, "#" + r.key), ...badges),
    r.en ? el("p", { class: "en" }, r.en) : null,
    ta,
    el("div", { class: "actions" }, btn, status));
}

async function search(q) {
  const box = $("#results");
  box.replaceChildren(el("p", { class: "empty" }, "Ricerca…"));
  try {
    const res = await fetch("/api/search?q=" + encodeURIComponent(q) + ($("#exact").checked ? "&exact=1" : ""));
    const rows = await res.json();
    if (!res.ok) throw new Error(rows.error || res.status);
    box.replaceChildren(...(rows.length ? rows.map(card) : [el("p", { class: "empty" }, "Nessun risultato.")]));
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

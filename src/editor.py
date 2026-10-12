"""Local Translator: the correction page of the contributors (http://suggestions.localhost:8770).

Only for contributors: the page is on when IT\\contributor.json exists, with the name the
suggestions are signed with, and optionally the "show keys" mode (string IDs in the game, instead
of the file IT\\show_keys.txt; see contributor()):

    {"author": "Ralkeon", "show_keys": "text"}

Search a text as seen in the game (Italian, English when the module knows it, or the string ID),
write the translation and save: it becomes a suggestion, a new row of the table suggestions of
map_it.db (lt_server.SUGGESTIONS_SCHEMA), and is sent to the game at once. The map, suggestions
included, is what contributors send to the author, who reviews them for the patch. A suggestion
counts until the patch entry of its key changes after it; "Annulla" deletes the last suggestion of
the key and the game goes back to the one before, the patch, or the translation of the module.

The server listens on 127.0.0.1 only, accepts only requests addressed to that host, localhost or
suggestions.localhost (no DNS rebinding) and saves only same-origin JSON requests (no other site can post to it).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import lt_server as lt
from patch_db import KIND_AUTO, KIND_DROP, KIND_REVIEWED, CachePatch, untagged

log = logging.getLogger("lt")

EDITOR_PORT = 8770
# browsers send every *.localhost name to 127.0.0.1 by themselves (RFC 6761): no hosts file
EDITOR_HOST = "suggestions.localhost"
CONTRIBUTOR_FILE = "contributor.json"  # in IT\: turns the page on, {"author": "<name>"}
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


@dataclass(frozen=True)
class Contributor:
    author: str
    # "show keys" mode (patch_db.key_tag): "text", "all", None = off; NOT_SET: the file
    # IT\show_keys.txt decides, as for everybody
    show_keys: str | None | object


NOT_SET = object()


def contributor(lang_dir: Path) -> Contributor | None:
    """The settings of IT\\contributor.json, None without the file (not a contributor):

        {"author": "Ralkeon", "show_keys": "text"}

    show_keys (optional): "text" (string IDs before the texts with words), "all" (every text,
    templates too), false (off); true = "text"."""
    path = lang_dir / CONTRIBUTOR_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("%s unreadable: %s", path.name, exc)
        return None
    author = data.get("author") if isinstance(data, dict) else None
    if not isinstance(author, str) or not author.strip():
        log.warning("%s: no \"author\"", path.name)
        return None
    mode = data.get("show_keys", NOT_SET)
    if mode is True:
        mode = "text"
    elif mode in (False, None, "off"):
        mode = None
    elif mode is not NOT_SET and mode not in ("text", "all"):
        log.warning("%s: show_keys must be \"text\", \"all\" or false, not %r", path.name, mode)
        mode = NOT_SET
    return Contributor(author.strip(), mode)


class Editor:
    def __init__(self, patch: CachePatch, keymap: lt.KeyMap, cache_db: Path, cache_key: str,
                 author: str) -> None:
        self.patch, self.keymap, self.cache_db, self.cache_key = patch, keymap, cache_db, cache_key
        self.author = author
        self.lock = threading.Lock()  # one save or undo at a time

    def _cache(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.cache_db}?mode=ro", uri=True, timeout=10)

    def _shown(self, key: int) -> tuple[str | None, int | None]:
        """(text, src_hash) of the key in cache.db, the text without its "show keys" prefix."""
        if not self.cache_db.is_file():
            return None, None
        con = self._cache()
        try:
            r = con.execute("SELECT text, src_hash FROM translations WHERE cache_key = ? AND id = ?",
                            (self.cache_key, key)).fetchone()
        finally:
            con.close()
        if not r:
            return None, None
        return (untagged(key, r[0]) if r[0] is not None else None), r[1]

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

    def row(self, key: int) -> dict:
        shown, h = self._shown(key)
        entry = self.patch.entry_version(key)
        patch = ({"kind": KINDS.get(entry[0]), "text": entry[1], "version": entry[2]}
                 if entry else None)
        mrow = self.keymap.get(key)
        history = self.keymap.suggestions(key)  # last first
        active = self.patch.valid_suggestion(key, h)
        last = history[0] if history else None
        en = (mrow[0] if mrow else "") or (last.en_source if last else "")
        # what "Annulla" goes back to: the suggestion before, the patch, the module's translation
        prev = None
        if history:
            before = history[1] if len(history) > 1 else None
            if before is not None:
                prev = {"what": "suggestion", "author": before.author, "text": before.suggestion}
            elif entry and entry[0] != KIND_DROP:
                prev = {"what": KINDS[entry[0]], "text": entry[1]}
            else:
                prev = {"what": "module", "text": mrow[1] if mrow else None}
        return {"key": key, "shown": shown, "en": en, "patch": patch,
                "suggestion": ({"text": last.suggestion, "author": last.author,
                                "version": last.version, "valid": active is not None}
                               if last else None),
                "undo": len(history), "prev": prev}

    def recent(self) -> list[dict]:
        return [{"key": s.key, "text": s.suggestion, "author": s.author, "version": s.version}
                for s in self.keymap.recent_suggestions()]

    def save(self, key: int, text: str) -> dict:
        if not text.strip():
            raise ValueError("empty text")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("text too long")
        with self.lock:
            shown, h = self._shown(key)
            mrow = self.keymap.get(key)
            if shown is None:  # the game does not hold it: what it would show
                entry = self.patch.entry_version(key)
                shown = entry[1] if entry and entry[0] != KIND_DROP else (mrow[1] if mrow else None)
            self.keymap.add_suggestion(key, mrow[0] if mrow else "", shown, text, self.author,
                                       self.patch.version, h if h else None)
            sent = self.patch.apply(self.cache_db, {key})
        log.info("editor: suggestion for key %d%s", key,
                 "" if sent else " (not sent: same text, or the game does not hold it yet)")
        return self.row(key)

    def undo(self, key: int) -> dict:
        """Delete the last suggestion of the key; the game goes back to the one before, the
        patch, or the translation of the module (the map's text)."""
        with self.lock:
            if not self.keymap.remove_last_suggestion(key):
                raise ValueError("nothing to undo")
            sent = self.patch.apply(self.cache_db, {key})
            shown, h = self._shown(key)
            entry = self.patch.entry_version(key)
            if not sent and self.patch.valid_suggestion(key, h) is None and (
                    entry is None or entry[0] == KIND_DROP):
                mrow = self.keymap.get(key)
                if mrow and mrow[1] and mrow[1] != shown:
                    sent = self.patch.send_text(key, h or 0, mrow[1])
                elif not mrow or not mrow[1]:
                    self._forget_in_cache(key)  # the addon asks again at the next start
        log.info("editor: suggestion for key %d undone", key)
        return self.row(key)

    def _forget_in_cache(self, key: int) -> None:
        if not self.cache_db.is_file():
            return
        con = sqlite3.connect(str(self.cache_db), timeout=10)
        try:
            with con:
                con.execute("DELETE FROM translations WHERE cache_key = ? AND id = ?",
                            (self.cache_key, key))
        finally:
            con.close()


def _handler(editor: Editor, port: int) -> type[BaseHTTPRequestHandler]:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"{EDITOR_HOST}:{port}"}
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
                    page = PAGE.replace("{{AUTHOR}}", _html(editor.author))
                    self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
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
            if path not in ("/api/save", "/api/undo"):
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
                if path == "/api/undo":
                    self._json(editor.undo(key))
                else:
                    self._json(editor.save(key, str(data["text"])))
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001
                log.warning("editor: %s failed: %s", path, exc)
                self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return Handler


def _html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def start(editor: Editor, port: int = EDITOR_PORT) -> ThreadingHTTPServer | None:
    """Serve the page in a thread; None (logged) if the port is taken."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _handler(editor, port))
    except OSError as exc:
        log.warning("editor page not started (port %d): %s", port, exc)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="editor").start()
    log.info("editor page: http://%s:%d", EDITOR_HOST, port)
    return server


PAGE = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Suggestion traduzione</title>
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1d2229; --muted: #646d78; --line: #dde1e6;
  --accent: #2f6fdb; --accent-text: #ffffff; --ok: #1e7f4f; --err: #b3261e;
  --badge-rev: #e3f1e8; --badge-auto: #eef0f3; --badge-sugg: #e6eefc; --badge-old: #fbeee0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15181c; --panel: #1e2227; --text: #e6e9ed; --muted: #9aa3ad; --line: #30363d;
    --accent: #5b8ff0; --accent-text: #0d1117; --ok: #5cc28c; --err: #f08b84;
    --badge-rev: #1f3a2b; --badge-auto: #2a2f36; --badge-sugg: #1f2f4d; --badge-old: #3d2e1c;
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
.b-sugg { background: var(--badge-sugg); } .b-old { background: var(--badge-old); }
.en { color: var(--muted); font-size: 13px; margin: 0 0 6px; white-space: pre-wrap; }
.note { font-size: 13px; margin: 6px 0 0; white-space: pre-wrap; }
.note-label { color: var(--muted); }
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
  <h1>Suggestion traduzione</h1>
  <p class="sub">Contributor: <b>{{AUTHOR}}</b>. Cerca il testo come lo vedi in gioco (o in inglese, o l'ID della stringa), scrivi la traduzione e salva: in gioco cambia in un paio di secondi, e la suggestion va a Nike con la tua map.</p>
  <form class="search" id="f">
    <input type="search" id="q" placeholder="Es. Utilizza l'attacco, oppure 37952" autofocus>
    <button type="submit">Cerca</button>
  </form>
  <label class="opt"><input type="checkbox" id="exact"> Cerca la stringa esatta
    <span class="hint">(solo i testi che sono esattamente questo, maiuscole comprese)</span></label>
  <div id="results"></div>
  <h2>Ultime suggestion</h2>
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

function prevLabel(p) {
  if (p.what === "suggestion") return "suggestion di " + p.author;
  if (p.what === "module") return "traduzione del modulo";
  return "patch, " + KIND[p.what];
}

function card(r, note) {
  const s = r.suggestion;
  const ta = el("textarea", {});
  ta.value = (s && s.valid ? s.text : null) ?? (r.patch ? r.patch.text : null) ?? r.shown ?? "";
  const status = el("span", { class: "status" + (note ? " ok" : "") }, note || "");
  const btn = el("button", { type: "button" }, "Salva");
  const undo = el("button", { type: "button", class: "ghost",
    title: r.undo ? "Elimina l'ultima suggestion (" + r.undo + " in tutto)" : "Nessuna suggestion da annullare" }, "Annulla");
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
  btn.onclick = () => post("/api/save", { key: r.key, text: ta.value }, "Salvataggio…", "Salvata, inviata al gioco");
  undo.onclick = () => post("/api/undo", { key: r.key }, "Annullamento…", "Annullata, ripristinato il testo precedente");
  const badges = [];
  const kind = r.patch ? r.patch.kind : null;
  badges.push(el("span", { class: "badge b-" + (kind || "none") },
    kind ? "patch: " + KIND[kind] : "non nella patch"));
  if (s && s.valid) badges.push(el("span", { class: "badge b-sugg" }, "suggestion di " + s.author));
  else if (s) badges.push(el("span", { class: "badge b-old", title: "La patch è cambiata dopo la suggestion, o l'inglese è cambiato: in gioco vale la patch" }, "suggestion superata"));
  self.append(...[
    el("div", { class: "head" }, el("span", { class: "key" }, "#" + r.key), ...badges),
    r.en ? el("p", { class: "en" }, r.en) : null,
    ta,
    s && !s.valid ? el("p", { class: "note" },
      el("span", { class: "note-label" }, "Suggestion superata di " + s.author + ": "), s.text) : null,
    r.prev ? el("p", { class: "note" },
      el("span", { class: "note-label" }, "Con Annulla torna (" + prevLabel(r.prev) + "): "),
      r.prev.text ?? "(nessun testo: lo ritraduce il modulo al prossimo avvio)") : null,
    el("div", { class: "actions" }, btn, undo, status)].filter((c) => c != null));
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
          el("span", {}, r.author + ", patch v" + r.version)),
        el("div", { class: "it" }, r.text))) : [el("p", { class: "empty" }, "Nessuna suggestion ancora.")]));
  } catch (e) { /* page still usable */ }
}

$("#f").onsubmit = (ev) => { ev.preventDefault(); const q = $("#q").value.trim(); if (q) search(q); };
$("#exact").onchange = () => { const q = $("#q").value.trim(); if (q) search(q); };
loadRecent();
</script>
</body>
</html>
"""

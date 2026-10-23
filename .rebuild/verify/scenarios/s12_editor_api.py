"""Contributors: the correction page's HTTP API on 127.0.0.1 (host and origin checks, search,
save, undo, recent, errors), suggestions sent to the game at once, answers from valid suggestions,
English learnt for them, contributor.json variants."""
import http.client
import json
import socket
import sqlite3

from addon import text_message

FAKE = ["--fake", "--no-update"]
PATCH = json.dumps({"version": 3, "strings": {"3": "Patch tre"}, "auto": {"4": "Auto quattro"},
                    "drop": [5], "glossary": "x"})
CACHE = [(1, 11, "Ciao"), (3, 33, "Patch tre"), (4, 44, "Auto quattro"), (6, 66, "Sei <c=@x>colore</c>")]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_map(a):
    path = a.module_dir / "IT" / "map_it.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE texts (key INTEGER PRIMARY KEY NOT NULL, en TEXT NOT NULL, it TEXT, how TEXT, seen INTEGER)")
    con.executemany("INSERT INTO texts VALUES (?, ?, ?, 'live', 0)",
                    [(1, "Hello", "Ciao"), (2, "Goodbye 100%", None), (3, "Patch text", "Patch tre"),
                     (6, "Six colour", "Sei colore")])
    con.commit()
    con.close()


class Client:
    def __init__(self, port):
        self.port = port

    def call(self, method, path, body=None, host=None, origin="same", ctype="application/json",
             length=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if origin == "same":
            origin = f"http://127.0.0.1:{self.port}"
        if origin:
            headers["Origin"] = origin
        if ctype:
            headers["Content-Type"] = ctype
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Length"] = str(length if length is not None else len(data))
        con.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers.items():
            con.putheader(k, v)
        con.endheaders(data)
        r = con.getresponse()
        raw = r.read()
        con.close()
        out = {"status": r.status,
               "headers": {k: r.getheader(k) for k in ("Content-Type", "Cache-Control",
                                                       "X-Content-Type-Options", "Content-Security-Policy")}}
        if r.getheader("Content-Type", "").startswith("application/json"):
            out["json"] = json.loads(raw)
        else:
            out["page"] = {"author_shown": None, "length_gt": len(raw) > 1000}
        return out, raw


def run(ctx):
    out = {}
    port = free_port()
    a = ctx.addon(args=[*FAKE, "--editor-port", str(port)], cache_rows=CACHE,
                  files={"IT/patch_it.json": PATCH,
                         "IT/contributor.json": "﻿" + json.dumps({"author": " Ann <&\"> "})})
    make_map(a)
    a.start()
    a.wait_handshake()
    a.wait_log("editor page")
    a.settle(1.0)
    c = Client(port)
    calls = []

    def call(name, *args, **kw):
        r, raw = c.call(*args, **kw)
        if "page" in r:
            r["page"]["author_shown"] = "<b>Ann &lt;&amp;&quot;&gt;</b>" in raw.decode()
        calls.append([name, r])
        a.settle(0.6)
        if r["status"] == 200 and path_is_change(args[1]):
            # "shown" is read from cache.db right after the text is sent: the addon may not have
            # saved it yet (also with the real addon). The settled state is read again here.
            r["json"]["shown"] = "<read while the addon saves>"
            again, _ = c.call("GET", f"/api/search?q={r['json']['key']}")
            calls.append([name + " -> settled", again])
        return r

    def path_is_change(path):
        return path in ("/api/save", "/api/undo")

    for host in (None, f"localhost:{port}", f"suggestions.localhost:{port}", "evil.example", "127.0.0.1:1"):
        call(f"page {host}", "GET", "/", host=host)
    call("not found", "GET", "/nope")
    for q in ("Ciao", "ciao", "hello", "HELLO", "3", "", "  ", "colore", "Sei colore", "100%", "_",
              "Patch tre"):
        call(f"search {q!r}", "GET", "/api/search?q=" + __import__("urllib.parse").parse.quote(q))
        call(f"search exact {q!r}", "GET", "/api/search?exact=1&q=" + __import__("urllib.parse").parse.quote(q))
    call("recent empty", "GET", "/api/recent")
    call("save 1", "POST", "/api/save", {"key": 1, "text": "Salve"})
    call("save 1 again", "POST", "/api/save", {"key": 1, "text": "Salve a tutti"})
    call("save 3 (patch)", "POST", "/api/save", {"key": 3, "text": "Tre corretto"})
    call("save 7 (unknown)", "POST", "/api/save", {"key": 7, "text": "Sette"})
    call("save 4 same text", "POST", "/api/save", {"key": 4, "text": "Auto quattro"})
    call("recent", "GET", "/api/recent")
    call("search 1", "GET", "/api/search?q=1")
    # the game asks for keys with suggestions: answered with them, English learnt
    a.send_many([text_message(1, 11, "Hello"), text_message(7, 77, "Seven"),
                 text_message(3, 99, "Patch text changed")])
    a.wait_answers([7], 10)
    a.settle(1.0)
    call("undo 1", "POST", "/api/undo", {"key": 1})
    call("undo 1 again", "POST", "/api/undo", {"key": 1})
    call("undo 1 nothing", "POST", "/api/undo", {"key": 1})
    call("undo 3", "POST", "/api/undo", {"key": 3})
    call("undo 7", "POST", "/api/undo", {"key": 7})
    call("search 7", "GET", "/api/search?q=7")
    for name, kw in (("bad origin", {"origin": "http://evil.example"}), ("no origin", {"origin": None}),
                     ("no json type", {"ctype": "text/plain"}), ("bad host", {"host": "evil.example"})):
        call(f"save {name}", "POST", "/api/save", {"key": 1, "text": "x"}, **kw)
    call("post other path", "POST", "/api/other", {"key": 1})
    call("bad json", "POST", "/api/save", b"{nope")
    call("no key", "POST", "/api/save", {"text": "x"})
    call("bad key", "POST", "/api/save", {"key": -1, "text": "x"})
    call("huge key", "POST", "/api/save", {"key": 2 ** 32, "text": "x"})
    call("text key", "POST", "/api/save", {"key": "abc", "text": "x"})
    call("empty text", "POST", "/api/save", {"key": 1, "text": "  "})
    call("too long", "POST", "/api/save", {"key": 1, "text": "è" * 40000})
    call("too big", "POST", "/api/save", {"key": 1, "text": "x"}, length=300000)
    a.close()
    out["calls"] = calls
    out["answers"] = a.answers()
    out["log"] = a.log_lines()
    out["suggestions"] = a.db("IT/map_it.db", "SELECT id, key, en_source, suggestion_source, suggestion, "
                                              "author, version, src_hash FROM suggestions")
    out["map"] = a.db("IT/map_it.db", "SELECT key, en, it, how FROM texts")
    out["cache_db"] = a.cache_rows()
    # contributor.json variants (page off with --editor-port 0)
    for name, body in (("no_author", json.dumps({"show_keys": "text"})), ("blank", json.dumps({"author": " "})),
                       ("broken", "{x"), ("list", "[1]")):
        b = ctx.addon(args=[*FAKE, "--editor-port", "0"], files={"IT/contributor.json": body})
        b.start()
        b.wait_handshake()
        b.settle(1.0)
        b.close()
        out[f"contributor_{name}"] = b.log_lines()
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    taken = busy.getsockname()[1]
    d = ctx.addon(args=[*FAKE, "--editor-port", str(taken)], files={"IT/contributor.json": json.dumps({"author": "Bo"})})
    d.start()
    d.wait_handshake()
    d.wait_log("editor page", 10)
    d.close()
    busy.close()
    out["port_taken"] = [x.replace(str(taken), "<port>") for x in d.log_lines()]
    return json.loads(json.dumps(out).replace(str(port), "<port>"))

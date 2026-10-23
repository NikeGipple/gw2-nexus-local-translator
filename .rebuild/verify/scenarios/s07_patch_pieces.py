"""The patch from GitHub in pieces: first download, answers from the patch (src_hash checks,
outdated hash, drop), the patch written into the game (texts sent without a request, rows removed
from cache.db, registries), a new version that changes only some pieces, an older version, the
same commit, a new width, the single-file fallback, a piece that does not match its index."""

DEFAULT_FP = "e7b9f216dc792a7a"  # fingerprint of the built-in default glossary (seen in the log)
ARGS = ["--fake", "--editor-port", "0"]


def h(n):
    return f"{n:016x}"


V1 = {
    10: {"kind": "strings", "text": "Rivista dieci", "src_hash": h(0x1010), "version": 1},
    11: {"kind": "strings", "text": "Rivista undici", "src_hash": h(0x1011), "version": 1},
    12: {"kind": "auto", "text": "Automatica dodici"},
    13: {"kind": "auto", "text": "Superata tredici", "src_hash": "0" * 16},
    14: {"kind": "drop"},
    150: {"kind": "strings", "text": "Nuova centocinquanta", "src_hash": h(0x150)},
    151: {"kind": "auto", "text": "Cambiata 151", "src_hash": h(0x151)},
    152: {"kind": "auto", "text": "Uguale 152", "src_hash": h(0x152)},
    153: {"kind": "strings", "text": "Vecchio inglese 153", "src_hash": h(0x153)},
    160: {"kind": "auto", "text": "Uscira' 160"},
    170: {"kind": "auto", "text": "Molto lunga " + "x" * 70000},
}
CACHE = [(10, 0x1010, "vecchio 10"), (14, 5, "da togliere 14"), (151, 0x151, "vecchio 151"),
         (152, 0x152, "Uguale 152"), (153, 0x999, "altro inglese 153"), (160, 0, "vecchio 160"),
         (500, 5, "non nella patch")]
ASK = [(10, 0x1010, "Ten"), (11, 0x2222, "Eleven changed"), (12, 77, "Twelve"),
       (13, 0x1313, "Thirteen"), (14, 5, "Fourteen"), (99, 9, "Not in patch")]


def snapshot(a):
    return {"patch": a.db("IT/patch_it.db", "SELECT key, kind, text, src_hash, piece, version FROM patch"),
            "pieces": a.db("IT/patch_it.db", "SELECT name, entries, size FROM pieces"),
            "meta": a.db("IT/patch_it.db", "SELECT k, v FROM meta"),
            "applied": a.db("IT/patch_it.db", "SELECT key, text FROM applied"),
            "dropped": a.db("IT/patch_it.db", "SELECT key FROM dropped"),
            "cache_db": a.cache_rows(), "files": a.files("IT")}


def phase(a, routes, ask=(), wait="patch update skipped|patch updated|up to date|older|single file|ignored"):
    a.set_routes(routes)
    if a.net_log.exists():
        a.net_log.unlink()
    a.start()
    a.wait_handshake()
    a.wait_log(wait, 30)
    a.settle(1.5)
    for key, hh, text in ask:
        a.send(key, hh, text)
    a.wait_answers([k for k, _, _ in ask], 20)
    a.settle(0.5)
    a.close()
    return {"answers": a.answers(), "log": a.log_lines(), "net": [r["url"] for r in a.net_requests()],
            **snapshot(a)}


def run(ctx):
    out = {}
    a = ctx.addon(args=ARGS, cache_rows=CACHE)
    gh = ctx.github()
    gh.commit("main", "1" * 40)
    gh.glossary("1" * 40, {"exact": {"Waypoint": "Waypoint"},
                           "terms": {"Lion's Arch": "Arco del Leone",
                                     "Divinity's Reach": "Divinity's Reach"}})
    gh.patch_pieces("1" * 40, 1, DEFAULT_FP, 100, V1)
    out["v1"] = phase(a, gh.routes, ASK)

    v2 = {k: v for k, v in V1.items() if k not in (151, 160)}
    v2[150] = {**V1[150], "text": "Cambiata centocinquanta", "version": 2}
    v2[151] = {"kind": "drop"}
    v2[250] = {"kind": "auto", "text": "Duecentocinquanta", "src_hash": h(0x250), "version": 2}
    gh2 = ctx.github()
    gh2.commit("main", "2" * 40)
    gh2.glossary("2" * 40, {"exact": {"Waypoint": "Waypoint"},
                            "terms": {"Lion's Arch": "Arco del Leone",
                                      "Divinity's Reach": "Divinity's Reach"}})
    gh2.patch_pieces("2" * 40, 2, DEFAULT_FP, 100, v2)
    out["v2"] = phase(a, gh2.routes, [(150, 0x150, "One fifty"), (250, 0x250, "Two fifty")])
    out["same_commit"] = phase(a, gh2.routes)

    gh3 = ctx.github()
    gh3.commit("main", "3" * 40)
    gh3.patch_pieces("3" * 40, 1, DEFAULT_FP, 100, V1)
    out["older"] = phase(a, gh3.routes)

    gh4 = ctx.github()
    gh4.commit("main", "4" * 40)
    gh4.patch_pieces("4" * 40, 3, "other-glossary", 1000, v2)
    out["new_width_other_glossary"] = phase(a, gh4.routes, [(12, 77, "Twelve"), (250, 0x250, "Two fifty")])

    gh5 = ctx.github()
    gh5.commit("main", "5" * 40)
    gh5.patch_pieces("5" * 40, 4, DEFAULT_FP, 1000, {**v2, 260: {"kind": "auto", "text": "Nuova"}})
    gh5.routes[next(u for u in gh5.routes if u.endswith("p0000.json"))]["length"] = 5
    out["bad_piece"] = phase(a, gh5.routes)

    b = ctx.addon(args=ARGS, cache_rows=CACHE)
    gh6 = ctx.github()  # no API, no pieces: the single file at the branch
    gh6.missing("main", "patch/it/index.json")
    gh6.raw("main", "patch/patch_it.json", __import__("json").dumps(
        {"version": 7, "strings": {"10": "Singola dieci"}, "auto": {"12": "Singola dodici"},
         "drop": [14], "glossary": DEFAULT_FP}))
    out["single_file"] = phase(b, gh6.routes, [(10, 0x1010, "Ten"), (12, 1, "Twelve")])
    gh7 = ctx.github()
    gh7.missing("main", "patch/it/index.json")
    gh7.raw("main", "patch/patch_it.json", __import__("json").dumps({"version": 6, "strings": {}}))
    out["single_file_older"] = phase(b, gh7.routes)
    return out

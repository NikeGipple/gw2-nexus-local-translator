"""Local patch sources and old files: --patch-file (developers' test patch), the v0.4 JSON files
moved into the archive, patch_it.json copied later by the tools (newer / older / damaged),
archives of older module versions (column h, no column version), the old suggestions table."""
import json
import sqlite3

DEFAULT_FP = "e7b9f216dc792a7a"
FAKE = ["--fake", "--editor-port", "0"]


def state(a):
    return {"patch": a.db("IT/patch_it.db", "SELECT key, kind, text, src_hash, piece, version FROM patch"),
            "meta": a.db("IT/patch_it.db", "SELECT k, v FROM meta WHERE k NOT IN ('file_sig')"),
            "has_file_sig": bool(a.db("IT/patch_it.db", "SELECT 1 FROM meta WHERE k = 'file_sig'")),
            "applied": a.db("IT/patch_it.db", "SELECT key, text FROM applied"),
            "skip": a.db("IT/patch_it.db", "SELECT key FROM skip"),
            "dropped": a.db("IT/patch_it.db", "SELECT key FROM dropped"),
            "tables": a.tables("IT/patch_it.db"), "cache_db": a.cache_rows()}


def session(a, ask=(), wait=None, routes=None):
    if routes is not None:
        a.set_routes(routes)
    a.start()
    a.wait_handshake()
    if wait:
        a.wait_log(wait, 30)
    a.settle(1.0)
    for key, hh, text in ask:
        a.send(key, hh, text)
    a.wait_answers([k for k, _, _ in ask], 20)
    a.settle(0.5)
    a.close()
    return {"answers": a.answers(), "log": a.log_lines(), **state(a)}


def run(ctx):
    out = {}
    # --patch-file: loaded, no patch download in this session, then GitHub wins again
    test = {"version": 50, "strings": {"1": "Prova uno"}, "auto": {"2": "Prova due"}, "drop": [3],
            "glossary": DEFAULT_FP}
    a = ctx.addon(args=[*FAKE, "--patch-file", "test_patch.json"],
                  files={"test_patch.json": json.dumps(test)}, cache_rows=[(3, 0, "tre")])
    a.args[a.args.index("test_patch.json")] = str(a.module_dir / "test_patch.json")
    out["patch_file"] = session(a, [(1, 1, "One"), (2, 2, "Two")], wait="glossary update skipped")
    out["patch_file"]["net"] = [r["url"] for r in a.net_requests()]
    gh = ctx.github()
    gh.commit("main", "c" * 40)
    gh.patch_pieces("c" * 40, 1, DEFAULT_FP, 100, {1: {"kind": "strings", "text": "GitHub uno " * 20}})
    a.args = list(FAKE)
    out["after_patch_file"] = session(a, [(1, 1, "One")], wait="patch updated", routes=gh.routes)
    # a piece smaller than its gzip-compressed form (GitHub compresses on the wire)
    tiny = ctx.addon(args=FAKE)
    gh_tiny = ctx.github()
    gh_tiny.commit("main", "d" * 40)
    gh_tiny.patch_pieces("d" * 40, 1, DEFAULT_FP, 100, {1: {"kind": "strings", "text": "Uno"}})
    out["tiny_gzip_piece"] = session(tiny, wait="patch update", routes=gh_tiny.routes)
    bad = ctx.addon(args=[*FAKE, "--no-update", "--patch-file", "nope.json"])
    out["patch_file_missing"] = session(bad)

    # v0.4 files
    v04 = {"IT/patch_it.json": json.dumps({"version": 3, "strings": {"10": "Dieci"},
                                           "auto": {"11": "Undici", "12": "Dodici"},
                                           "drop": [13], "glossary": "old-fp"}),
           "IT/patch_it.applied.json": json.dumps({"11": "Undici", "x": "bad", "14": 5}),
           "IT/patch_it.skip.json": json.dumps({"version": 3, "keys": [12, "15"]}),
           "IT/patch_it.dropped.json": json.dumps([13, "x", 16]),
           "IT/patch_it.ids_failed.json": "not json"}
    b = ctx.addon(args=[*FAKE, "--no-update"], files=v04,
                  cache_rows=[(11, 0, "Undici"), (12, 0, "vecchio dodici")])
    out["migrate"] = session(b, [(10, 1, "Ten"), (12, 2, "Twelve")])
    out["migrate"]["files"] = b.files("IT")
    # ids_failed set by the migration: the patch is off; a newer patch_it.json is imported
    newer = json.dumps({"version": 4, "strings": {"10": "Dieci nuova"}, "glossary": DEFAULT_FP})
    (b.module_dir / "IT" / "patch_it.json").write_text(newer, encoding="utf-8")
    out["reimport_newer"] = session(b, [(10, 1, "Ten")])
    (b.module_dir / "IT" / "patch_it.json").write_text(
        json.dumps({"version": 2, "strings": {"10": "Dieci vecchia"}}), encoding="utf-8")
    out["reimport_older"] = session(b)
    out["reimport_same_file"] = session(b)
    (b.module_dir / "IT" / "patch_it.json").write_text("{broken", encoding="utf-8")
    out["reimport_broken"] = session(b)

    c = ctx.addon(args=[*FAKE, "--no-update"],
                  files={"IT/patch_it.json": json.dumps({"version": 1, "strings": {"x": "bad"}})})
    out["migrate_invalid_patch"] = session(c)

    # archives of older versions
    for name, schema in (
            ("archive_h", "CREATE TABLE patch (key INTEGER PRIMARY KEY NOT NULL, kind INTEGER NOT NULL, "
                          "text TEXT, h TEXT, piece INTEGER); CREATE TABLE stale (key INTEGER);"),
            ("archive_no_version", "CREATE TABLE patch (key INTEGER PRIMARY KEY NOT NULL, kind INTEGER "
                                   "NOT NULL, text TEXT, src_hash INTEGER, piece INTEGER);")):
        d = ctx.addon(args=[*FAKE, "--no-update"])
        db = d.module_dir / "IT" / "patch_it.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        con.executescript(schema + "CREATE TABLE pieces (name TEXT PRIMARY KEY NOT NULL, sha256 TEXT NOT NULL, "
                          "entries INTEGER, size INTEGER); CREATE TABLE meta (k TEXT PRIMARY KEY NOT NULL, v TEXT);")
        con.execute("INSERT INTO patch (key, kind, text, piece) VALUES (5, 0, 'Cinque', 0)")
        con.execute("INSERT INTO pieces VALUES ('p0000.json', 'abc', 1, 10)")
        con.executemany("INSERT INTO meta VALUES (?, ?)", [("commit", "abc"), ("version", "9"),
                                                           ("source", "pieces"), ("migrated", "1")])
        con.commit()
        con.close()
        out[name] = session(d, [(5, 1, "Five")])

    # map with the suggestions table of the first test builds
    e = ctx.addon(args=[*FAKE, "--no-update"])
    mp = e.module_dir / "IT" / "map_it.db"
    mp.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(mp)
    con.executescript("CREATE TABLE suggestions (id INTEGER PRIMARY KEY, key INTEGER NOT NULL, source TEXT "
                      "NOT NULL, suggestion TEXT NOT NULL, author TEXT NOT NULL, version INTEGER NOT NULL, "
                      "src_hash INTEGER); INSERT INTO suggestions VALUES (1, 7, 'Seven', 'Sette', 'Ann', 0, NULL);")
    con.commit()
    con.close()
    out["old_suggestions"] = session(e)
    out["old_suggestions"]["map_tables"] = e.tables("IT/map_it.db")
    out["old_suggestions"]["rows"] = e.db("IT/map_it.db", "SELECT * FROM suggestions")
    return out

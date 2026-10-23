"""Developer commands of the exe (--export-review, --import-review), the cache.db watcher (cache.db
created by the addon after the start, or moved later to the v0.2.0 layout), and the cache built
with older protection rules or an older snapshot format (lines translated again)."""
import csv
import io
import json
import sqlite3
import time

from addon import CACHE_SCHEMA

FAKE = ["--fake", "--editor-port", "0", "--no-update"]


def make_map(a, rows):
    path = a.module_dir / "IT" / "map_it.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE texts (key INTEGER PRIMARY KEY NOT NULL, en TEXT NOT NULL, it TEXT, how TEXT, seen INTEGER)")
    con.executemany("INSERT INTO texts VALUES (?, ?, ?, 'live', 0)", rows)
    con.commit()
    con.close()


def commands(ctx):
    out = {}
    patch = {"version": 2, "strings": {"1": "Uno"}, "auto": {"2": "Due"}}
    a = ctx.addon(args=["--export-review", "review.csv"], files={"IT/patch_it.json": json.dumps(patch)})
    make_map(a, [(1, "One", "Uno?"), (3, "Three", "Tre")])
    out["export"] = {**a.run_command(), "csv": None}
    csv_path = a.workdir / "review.csv"
    out["export"]["csv"] = csv_path.read_bytes().decode("utf-8") if csv_path.exists() else None
    rows = list(csv.reader(io.StringIO(out["export"]["csv"].lstrip("﻿")), delimiter=";"))
    rows[1][4], rows[3][4] = "Uno rivisto", "Tre rivisto"
    with open(a.workdir / "reviewed.csv", "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f, delimiter=";").writerows(rows)
    a.args = ["--import-review", "reviewed.csv"]
    out["import_without_patch_file"] = a.run_command()
    a.args = ["--import-review", "reviewed.csv", "--patch-file", str(a.module_dir / "IT" / "patch_it.json")]
    out["import"] = {**a.run_command(), "patch": a.file_text("IT/patch_it.json")}
    a.args = ["--export-review", "pt.csv", "--lang", "pt_br"]
    out["export_pt_br"] = a.run_command()
    return out


def watcher(ctx):
    out = {}
    patch = json.dumps({"version": 1, "strings": {"1": "Uno"}, "glossary": "x"})
    a = ctx.addon(args=FAKE, files={"IT/patch_it.json": patch}, cache_db=None)
    a.start()
    a.wait_handshake()
    a.settle(1.0)
    con = sqlite3.connect(a.cache_db)
    con.execute(CACHE_SCHEMA)
    con.execute("INSERT INTO translations VALUES (1, 'it', 0, 1, 0, 'vecchio')")
    con.commit()
    con.close()
    a.wait_log("translations sent", 15)
    a.settle(0.5)
    a.close()
    out["created_later"] = {"log": a.log_lines(), "cache_db": a.cache_rows(), "answers": a.answers()}
    b = ctx.addon(args=FAKE, files={"IT/patch_it.json": patch}, cache_db="old")
    b.start()
    b.wait_handshake()
    b.settle(1.0)
    con = sqlite3.connect(b.cache_db)  # the addon moves cache.db to its new layout
    con.execute("DROP TABLE translations")
    con.execute(CACHE_SCHEMA)
    con.execute("INSERT INTO translations VALUES (1, 'it', 0, 1, 0, 'vecchio')")
    con.commit()
    con.close()
    b.wait_log("translations sent", 15)
    b.settle(0.5)
    b.close()
    out["old_layout"] = {"log": b.log_lines(), "cache_db": b.cache_rows(), "answers": b.answers()}
    return out


SNAPSHOT_LINES = {"Edible Mushroom[s]": "vecchio funghi", "Large Bone[s]": "vecchio ossa",
                  "Gives 5% power": "Dà 5% potere", "Hello": "Ciao",
                  "Win %num1% game[s]": "Vinci %num1% partite"}


def protect(ctx):
    out = {}
    glossary = {"terms": {"Large Bone": "Osso grande"}}
    for name, snapshot in (
            ("protect_1", {"terms": glossary["terms"], "exact": {}, "protect": 1}),
            ("protect_3", {"terms": glossary["terms"], "exact": {}, "protect": 3}),
            ("protect_4", {"terms": glossary["terms"], "exact": {}, "protect": 4}),
            ("terms_only_format", glossary["terms"]),
            ("exact_missing", {"terms": glossary["terms"], "protect": 5}),
            ("damaged", "not json")):
        files = {"IT/glossary_it.json": json.dumps(glossary),
                 "IT/cache_it.glossary.json": snapshot if isinstance(snapshot, str) else json.dumps(snapshot),
                 "IT/cache_it.jsonl": "".join(json.dumps({"s": s, "t": t}) + "\n" for s, t in SNAPSHOT_LINES.items())
                 + "not a json line\n"}
        a = ctx.addon(args=FAKE, files=files)
        a.start()
        a.wait_handshake()
        a.settle(1.0)
        a.close()
        out[name] = {"log": a.log_lines(), "cache": sorted(a.file_text("IT/cache_it.jsonl").splitlines()),
                     "snapshot": json.loads(a.file_text("IT/cache_it.glossary.json"))}
    return out


def run(ctx):
    return {"commands": commands(ctx), "watcher": watcher(ctx), "protect": protect(ctx)}

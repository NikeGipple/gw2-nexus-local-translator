"""Developer functions of the library: build_auto (tools/patch_llm.py), Progress, and the review
CSV round trip (export_review / import_review, also reachable from the module's CLI)."""
import contextlib
import csv
import io
import json
import sqlite3
import sys
import time

from _common import WORK, attempt, emit

import lt_server as L

clock = [1000.0]
time.monotonic = lambda: clock[0]  # Progress prints elapsed and remaining time


def make_map(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE texts (key INTEGER PRIMARY KEY, en TEXT NOT NULL, it TEXT, how TEXT, seen INTEGER)")
    con.executemany("INSERT INTO texts VALUES (?, ?, ?, 'live', 0)", rows)
    con.commit()
    con.close()


MAP_ROWS = [
    (1, "Hello world", "Ciao mondo"), (2, "Pact Longbow", "Arco lungo Pact"),
    (3, "Recipe: Pact Longbow", None), (4, "Thief", "Ladro"), (5, "Gives 5% power", None),
    (6, "Lion's Arch", None), (7, "First\nSecond", None), (8, "\x01garbled", None),
    (9, "Untranslated", None), (10, "Reviewed one", None), (11, "Citizen", None),
    (12, "Large Bone[s]", None), (13, "Box[pl:\"Boxes\"]", None), (14, "Nothing", None),
]
PATCH = {"version": 2, "strings": {"10": "Rivista", "50": "Solo nella patch"},
         "auto": {"9": "vecchia", "60": "fuori dalla mappa", "14": "Niente"}, "drop": [70],
         "glossary": "old"}
GLOSSARY = {"exact": {"Pact Longbow": "Arco lungo Pact"},
            "terms": {"Lion's Arch": "Arco del Leone"}, "feminine": {"Cittadino": "Cittadina"}}
OUTPUTS = {"Hello world": "Ciao mondo", "Thief": "Ladro/a", "Gives 5% power": "Dà 5% potere",
           "First": "Primo", "Second": "Secondo", "Citizen": "Cittadino",
           "Large Bone[s]": "Ossa grandi", "Box[pl:\"Boxes\"]": "Box[pl:\"Boxes\"]",
           "Nothing": ""}


def build_auto():
    out = {}
    m, p = WORK / "map.db", WORK / "patch_it.json"
    make_map(m, MAP_ROWS)
    p.write_text(json.dumps(PATCH), encoding="utf-8")
    g = L.Glossary()
    g.load_dict(GLOSSARY)
    asked = []

    def translate_lines(lines):
        asked.append(list(lines))
        clock[0] += 3
        return {x: OUTPUTS[x] for x in lines if x in OUTPUTS}

    translations = {"Untranslated": "Untranslated"}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sys.stdout = buf
        r = attempt(L.build_auto, m, p, g, translations, translate_lines, "prova", 2)
        sys.stdout = sys.__stdout__
    out["result"] = r
    out["asked"] = asked
    out["printed"] = buf.getvalue()
    out["patch"] = p.read_text(encoding="utf-8")
    out["translations"] = translations
    # second run: nothing changes, the file stays as it is
    before = p.stat().st_mtime_ns
    with contextlib.redirect_stdout(io.StringIO()):
        out["again"] = attempt(L.build_auto, m, p, g, dict(translations), translate_lines)
    out["again_rewritten"] = p.stat().st_mtime_ns != before
    return out


def progress():
    buf = io.StringIO()
    pr = L.Progress(10, "lavoro")
    pr.out = buf
    for step, dt in ((1, 0.5), (2, 2), (3, 4000), (4, 1)):
        clock[0] += dt
        pr.add(step)
    silent = L.Progress(0, "niente")
    silent.out = None
    silent.add(1)
    return {"printed": buf.getvalue(), "hms": [L.Progress._hms(x) for x in (0, 59.9, 61, 3600, 7322)]}


def review():
    out = {}
    m, p, csvf = WORK / "rmap.db", WORK / "rpatch.json", WORK / "review.csv"
    make_map(m, [(1, "Hello", "Ciao"), (2, "Bye", None), (3, "Thanks", "Grazie")])
    p.write_text(json.dumps({"version": 1, "strings": {"3": "Grazie mille"}, "auto": {"2": "Addio", "4": "Quattro"}}),
                 encoding="utf-8")
    out["export_rows"] = L.export_review(m, p, csvf)
    out["export_csv"] = csvf.read_bytes().decode("utf-8")
    out["export_no_map"] = L.export_review(WORK / "none.db", p, WORK / "nomap.csv")
    rows = list(csv.reader(io.StringIO(out["export_csv"].lstrip("﻿")), delimiter=";"))
    rows[1][4], rows[2][4], rows[3][4] = "Salve", "-", "Grazie mille"
    rows.append(["x", "", "", "", "ignored"])
    rows.append(["9", "", "", "", "  "])
    rows.append(["4", "", "", "", "Quattro rivista"])
    rows.append(["5", "", "", "", "-"])
    with open(WORK / "reviewed.csv", "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f, delimiter=";").writerows(rows)
    out["import"] = L.import_review(WORK / "reviewed.csv", p)
    out["patch_after"] = p.read_text(encoding="utf-8")
    with open(WORK / "comma.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([L.REVIEW_COLUMNS, ["3", "", "", "", "-"], ["1", "", "", "", "Salve"]])
    out["import_comma"] = L.import_review(WORK / "comma.csv", p)
    out["patch_after_comma"] = p.read_text(encoding="utf-8")
    out["columns"] = L.REVIEW_COLUMNS
    return out


emit({"build_auto": build_auto(), "progress": progress(), "review": review()})

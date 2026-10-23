"""The string IDs check: the first 50 known texts the addon sends are compared with the map; if
most differ, the patch is taken out of cache.db and stays off (also after a restart) until a check
passes again. Contributors' rows ('contrib...') do not count; HTML entities and spaces do not."""
import json
import sqlite3

from addon import text_message

FAKE = ["--fake", "--editor-port", "0", "--no-update"]


def session(a, *batches):
    """Each batch is sent in one write and fully answered (and saved by the addon) before the next:
    the check and its cache.db work run in a thread, so this keeps the result repeatable."""
    a.start()
    a.wait_handshake()
    a.settle(1.0)
    for sends in batches:
        a.send_many([text_message(k, k, t) for k, t in sends])
        a.wait_answers([k for k, _ in sends], 30)
        a.settle(1.5)
    a.close()
    return {"answers": a.answers(), "log": a.log_lines(), "cache_db": a.cache_rows(),
            "ids_failed": [{**json.loads(v), "time": "<time>" if "time" in json.loads(v) else None}
                           for _k, v in a.db("IT/patch_it.db", "SELECT k, v FROM meta WHERE k = 'ids_failed'")],
            "applied": a.db("IT/patch_it.db", "SELECT key FROM applied")}


def run(ctx):
    patch = {"version": 1, "strings": {str(k): f"Patch {k}" for k in range(1, 61)},
             "auto": {"70": "Auto 70"}, "glossary": "fp"}
    a = ctx.addon(args=FAKE, files={"IT/patch_it.json": __import__("json").dumps(patch)},
                  cache_rows=[(k, 0, f"Patch {k}") for k in range(1, 61)] + [(70, 0, "Auto 70")])
    mp = a.module_dir / "IT" / "map_it.db"
    con = sqlite3.connect(mp)
    con.execute("CREATE TABLE texts (key INTEGER PRIMARY KEY NOT NULL, en TEXT NOT NULL, it TEXT, how TEXT, seen INTEGER)")
    con.executemany("INSERT INTO texts VALUES (?, ?, NULL, 'live', 0)",
                    [(k, f"Text {k} &amp; more ") for k in [*range(1, 61), 80]])
    con.executemany("INSERT INTO texts VALUES (?, ?, NULL, 'contrib-ann', 0)",
                    [(k, f"Text {k}") for k in range(100, 160)])
    con.commit()
    con.close()
    out = {}
    # contributors' keys (ignored), 20 equal, 29 different; the 50th (not in the patch) decides
    first = [(k, f"Other {k}") for k in range(100, 130)]
    first += [(k, f"Text {k} & more") for k in range(1, 21)]
    first += [(k, f"Changed {k}") for k in range(21, 50)]
    out["fail"] = session(a, first, [(80, "Changed 80")], [(k, f"Changed {k}") for k in range(50, 56)])
    out["still_off"] = session(a, [(k, f"Changed {k}") for k in range(1, 10)])
    # the map learnt the English of the answers given before the check failed
    same = [(k, f"Changed {k}" if k < 10 or k > 20 else f"Text {k} & more") for k in range(1, 50)]
    out["pass"] = session(a, same, [(80, "Text 80 & more")], [(56, "Text 56 & more")])
    return out

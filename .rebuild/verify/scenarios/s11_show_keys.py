""""Show keys" mode: texts start with their string ID. The file show_keys.txt (text / all / empty /
tutte), contributor.json's show_keys (wins over the file), prefixes added to and removed from
cache.db at start, patch texts written with the prefix, and the --show-keys command."""
import json

FAKE = ["--fake", "--editor-port", "0", "--no-update"]
TEXTS = [(1, "Hello there"), (2, "[m]%str1%"), (3, "%str1% %str2%"), (4, "[null]"), (5, "   "),
         (6, "Patch key"), (7, "Line one\nline two")]
PATCH = json.dumps({"version": 1, "strings": {"6": "Chiave della patch", "8": "Otto"},
                    "glossary": "x"})
CACHE = [(8, 0, "vecchio otto"), (9, 0, "9 - gia' con prefisso"), (10, 0, "[m]%str1%"),
         (11, 0, "Testo")]


def session(a, ask=True):
    a.start()
    a.wait_handshake()
    a.settle(1.0)
    if ask:
        for k, t in TEXTS:
            a.send(k, k, t)
        a.wait_answers([k for k, _ in TEXTS], 20)
        a.settle(0.5)
    a.close()
    return {"answers": a.answers(), "log": a.log_lines(), "cache_db": a.cache_rows()}


def run(ctx):
    out = {}
    for name, files in (("text", {"IT/show_keys.txt": "text\n"}),
                        ("all", {"IT/show_keys.txt": "ALL extra words"}),
                        ("tutte", {"IT/show_keys.txt": "tutte"}),
                        ("empty_file", {"IT/show_keys.txt": ""}),
                        ("contributor_all", {"IT/show_keys.txt": "text",
                                             "IT/contributor.json": json.dumps({"author": "Ann", "show_keys": "all"})}),
                        ("contributor_off", {"IT/show_keys.txt": "text",
                                             "IT/contributor.json": json.dumps({"author": "Ann", "show_keys": False})}),
                        ("contributor_true", {"IT/contributor.json": json.dumps({"author": "Ann", "show_keys": True})}),
                        ("contributor_bad", {"IT/show_keys.txt": "all",
                                             "IT/contributor.json": json.dumps({"author": "Ann", "show_keys": "x"})})):
        a = ctx.addon(args=FAKE, files={**files, "IT/patch_it.json": PATCH}, cache_rows=CACHE)
        out[name] = session(a)
        if name == "text":
            (a.module_dir / "IT" / "show_keys.txt").unlink()
            out["text_then_off"] = session(a, ask=False)
    # a cache.db row without text
    n = ctx.addon(args=FAKE, files={"IT/show_keys.txt": "text", "IT/patch_it.json": PATCH},
                  cache_rows=CACHE + [(12, 0, None)])
    out["null_text_row"] = session(n)
    # the command, with the game closed
    b = ctx.addon(args=["--show-keys", "all"], cache_rows=CACHE, files={"IT/.keep": ""})
    out["cmd_all"] = {**b.run_command(), "file": b.file_text("IT/show_keys.txt"), "cache_db": b.cache_rows()}
    b.args = ["--show-keys", "text"]
    out["cmd_text"] = {**b.run_command(), "file": b.file_text("IT/show_keys.txt"), "cache_db": b.cache_rows()}
    b.args = ["--show-keys", "off"]
    out["cmd_off"] = {**b.run_command(), "file": b.file_text("IT/show_keys.txt"), "cache_db": b.cache_rows()}
    out["cmd_off_again"] = b.run_command()
    c = ctx.addon(args=["--show-keys", "text"], cache_db=None, files={"IT/.keep": ""})
    out["cmd_no_cache_db"] = {**c.run_command(), "file": c.file_text("IT/show_keys.txt")}
    d = ctx.addon(args=["--show-keys", "bogus"])
    out["cmd_bad_value"] = d.run_command()
    e = ctx.addon(args=["--show-keys", "text"])  # fresh module folder: no IT folder yet
    out["cmd_no_lang_dir"] = e.run_command()
    return out

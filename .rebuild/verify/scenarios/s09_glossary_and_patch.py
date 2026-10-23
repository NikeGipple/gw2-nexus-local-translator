"""A glossary change with a patch in place: reviewed rows are never removed, automatic rows made
with another glossary are removed from cache.db and then skipped (local translation) until a patch
made with the player's glossary arrives; local translations with the old terms are removed."""
import json
import re

FAKE = ["--fake", "--editor-port", "0"]
G1 = {"terms": {"Dragon": "Drago"}}
G2 = {"terms": {"Dragon": "Dragone"}}
SHA = "e" * 40


def session(a, routes=None, ask=(), wait=()):
    if routes is not None:
        a.set_routes(routes)
    a.start()
    a.wait_handshake()
    for w in wait:
        a.wait_log(w, 30)
    a.settle(1.5)
    for key, hh, text in ask:
        a.send(key, hh, text)
    a.wait_answers([k for k, _, _ in ask], 20)
    a.settle(0.5)
    a.close()
    return {"answers": a.answers(), "log": a.log_lines(), "cache_db": a.cache_rows(),
            "skip": a.db("IT/patch_it.db", "SELECT key FROM skip"),
            "meta": a.db("IT/patch_it.db", "SELECT k, v FROM meta WHERE k NOT IN ('file_sig')"),
            "applied": a.db("IT/patch_it.db", "SELECT key, text FROM applied"),
            "cache": sorted(a.jsonl("IT/cache_it.jsonl") or [], key=repr)}


def run(ctx):
    out = {}
    a = ctx.addon(args=[*FAKE, "--no-update"], files={"IT/glossary_it.json": json.dumps(G1)},
                  cache_rows=[(1, 0, "[IT] The Drago sleeps!"), (2, 0, "x [IT] The Drago sleeps"),
                              (3, 0, "Altro testo")])
    out["local_translation"] = session(a, ask=[(4, 4, "The Dragon sleeps")])
    gh = ctx.github()
    gh.commit("main", SHA)
    gh.glossary(SHA, G1, etag='"g1"')
    (a.module_dir / "IT" / "glossary_it.etag").write_text('"g1"')
    entries = {1: {"kind": "strings", "text": "[IT] The Drago sleeps!"},
               2: {"kind": "auto", "text": "x [IT] The Drago sleeps"},
               3: {"kind": "auto", "text": "Altro testo"}}
    gh.patch_pieces(SHA, 1, "made-with-another-glossary", 100, entries)
    a.args = list(FAKE)
    out["patch_v1"] = session(a, gh.routes, wait=["patch updated", "glossary up to date"])
    gh.glossary(SHA, G2, etag='"g2"')
    out["glossary_changed"] = session(a, gh.routes, ask=[(2, 2, "Two"), (3, 3, "Three")],
                                      wait=["glossary updated", "patch up to date"])
    fp = re.search(r"glossary updated: (\w+)", " ".join(out["glossary_changed"]["log"])).group(1)
    gh2 = ctx.github()
    gh2.commit("main", "f" * 40)
    gh2.glossary("f" * 40, G2, etag='"g2"')
    gh2.patch_pieces("f" * 40, 2, fp, 100, entries)
    out["patch_with_my_glossary"] = session(a, gh2.routes, ask=[(2, 2, "Two")],
                                            wait=["patch updated", "glossary up to date"])
    return out

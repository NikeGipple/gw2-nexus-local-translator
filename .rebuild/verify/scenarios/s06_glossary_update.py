"""The glossary from GitHub: download at the latest commit (API) with ETag, 304, fallback to the
branch URL, invalid or cut files, --branch; what a change does to the local cache, to cache.db
(old translations removed) and to the answers that follow."""
import json

V1 = {"exact": {"Waypoint": "Punto di passaggio", "Bank": "Banca"},
      "terms": {"Lion's Arch": "Arco del Leone", "Dragon": "Drago"}}
V2 = {"exact": {"Waypoint": "Waypoint", "Trading Post": "Mercato"},
      "terms": {"Lion's Arch": "Arco del Leone", "Dragon": "Dragone", "Tyria": "Tyria"}}
TEXTS = ["Waypoint", "Bank", "Trading Post", "Go to Lion's Arch", "The Dragon wakes",
         "Tyria is big", "Hello"]


def ask(a, base):
    for i, t in enumerate(TEXTS):
        a.send(base + i, base + i, t)
    a.wait_answers(range(base, base + len(TEXTS)), 30)


def no_patch(gh, ref):
    gh.missing(ref, "patch/it/index.json")
    gh.missing(ref, "patch/patch_it.json")


def run(ctx):
    out = {}
    gh = ctx.github()
    gh.commit("main", "a" * 40)
    gh.glossary("a" * 40, V1, etag='"g1"')
    no_patch(gh, "a" * 40)
    a = ctx.addon(args=["--fake", "--editor-port", "0"], routes=gh.routes)
    a.start()
    a.wait_handshake()
    ask(a, 1)                       # built-in default glossary
    a.wait_log("glossary updated")
    a.wait_log("patch update skipped")
    a.settle(1.0)
    ask(a, 101)                     # glossary V1
    a.settle(0.5)
    first_cache = sorted(a.jsonl("IT/cache_it.jsonl"), key=repr)
    a.close()
    out["download"] = {"answers": a.answers(), "log": a.log_lines(), "net": a.net_requests(),
                       "cache": first_cache, "glossary_file": a.file_text("IT/glossary_it.json"),
                       "etag": a.file_text("IT/glossary_it.etag"), "cache_db": a.cache_rows(),
                       "snapshot": json.loads(a.file_text("IT/cache_it.glossary.json"))}
    # same commit, same ETag: 304
    a.net_log.unlink()
    a.start()
    a.wait_handshake()
    a.wait_log("glossary up to date")
    a.wait_log("patch update skipped")
    a.close()
    out["not_modified"] = {"log": a.log_lines(), "net": a.net_requests()}
    # API unreachable: the branch URL; V2 changes exact and terms
    gh2 = ctx.github()
    gh2.glossary("main", V2, etag='"g2"')
    no_patch(gh2, "main")
    a.set_routes(gh2.routes)
    a.net_log.unlink()
    a.start()
    a.wait_handshake()
    a.wait_log("glossary updated")
    a.wait_log("patch update skipped")
    a.settle(1.0)
    ask(a, 201)
    a.settle(0.5)
    a.close()
    out["changed"] = {"answers": {k: v for k, v in a.answers().items() if int(k) >= 201},
                      "log": a.log_lines(), "net": a.net_requests(),
                      "cache": sorted(a.jsonl("IT/cache_it.jsonl"), key=repr),
                      "cache_db": a.cache_rows(), "etag": a.file_text("IT/glossary_it.etag"),
                      "snapshot": json.loads(a.file_text("IT/cache_it.glossary.json"))}
    # broken downloads: nothing replaced
    for name, route in (("invalid", {"text": json.dumps({"patterns": {"Longbow": "Arco"}})}),
                        ("not_json", {"text": "{"}),
                        ("cut", {"text": json.dumps(V1), "length": 99999}),
                        ("server_error", {"status": 500})):
        b = ctx.addon(args=["--fake", "--editor-port", "0", "--branch", "dev"],
                      files={"IT/glossary_it.json": json.dumps(V2), "IT/glossary_it.etag": '"g2"'})
        routes = {"https://raw.githubusercontent.com/NikeGipple/gw2-nexus-local-translator/dev/"
                  "glossary/glossary_it.json": route}
        b.set_routes(routes)
        b.start()
        b.wait_handshake()
        b.wait_log("glossary update skipped")
        b.wait_log("patch update skipped")
        b.close()
        out[name] = {"log": b.log_lines(), "net": b.net_requests(),
                     "file_kept": b.file_text("IT/glossary_it.json") == json.dumps(V2),
                     "files": b.files("IT")}
    return out

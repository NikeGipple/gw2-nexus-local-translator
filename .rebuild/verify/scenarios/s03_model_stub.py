"""The model in the module, with a scripted CTranslate2/SentencePiece (stub): how it is loaded
(threads, CPU), what it receives (tokens, end-of-sentence, length limits, batches of 24), failed
batches (asked again, then left in English), the cache file, and requests that wait for the
model while it loads."""

STUB_FILES = {"IT/model/model.bin": "x", "IT/model/source.spm": "x", "IT/model/target.spm": "x"}
NO_UPDATE = ["--no-update", "--editor-port", "0"]


def lines(n):
    return [f"Sentence number {i} here" for i in range(n)]


def run(ctx):
    out = {}
    # 1) lines sent while the model loads: batches of 24 in arrival order; the end-of-sentence
    #    token, the 400-token cut and the length limit; then a batch that fails twice
    from addon import text_message
    script = {"outputs": {"Bad line": "", "Thief": "Ladro/a"}, "fail": ["Broken line"],
              "load_delay": 1.5}
    a = ctx.addon(args=[*NO_UPDATE, "--threads", "1"], files=STUB_FILES, stub_ct2=script)
    a.start()
    a.wait_handshake()
    texts = lines(30) + ["Thief", "Bad line", "Long " + "x" * 450, "Thief\nBad line"]
    a.send_many([text_message(i + 1, i, t) for i, t in enumerate(texts)])
    a.wait_answers(range(1, len(texts) + 1), 60)
    a.send_many([text_message(100, 7, "Broken line"), text_message(102, 8, "Friend line")])
    a.wait_answers([100, 102], 30)
    a.send(101, 7, "Broken line")  # given up: answered in English at once
    a.wait_answers([101], 30)
    a.settle(1.0)
    a.close()
    calls = a.ct2_calls()
    out["first"] = {"answers": a.answers(), "log": a.log_lines(),
                    "init": [c for c in calls if "init" in c],
                    "batches": [[c["batch"], c["ends"], c["beam_size"], c["max_decoding_length"]]
                                for c in calls if "batch" in c],
                    "cache": sorted(a.jsonl("IT/cache_it.jsonl"), key=repr)}
    # 2) threads: default (a quarter of the CPUs, at most 2) and too many
    for name, extra in (("default", []), ("many", ["--threads", "999"]), ("zero", ["--threads", "0"])):
        b = ctx.addon(args=[*NO_UPDATE, *extra], files=STUB_FILES, stub_ct2={})
        b.start()
        b.wait_log("model loaded")
        b.close()
        import os
        cpus = os.cpu_count() or 2
        threads = [c["init"]["intra_threads"] for c in b.ct2_calls() if "init" in c]
        out[f"threads_{name}"] = {"cpus_rule": [t == min(999, cpus) if name == "many"
                                                else t == max(1, min(2, cpus // 4)) for t in threads],
                                  "log": [x for x in b.log_lines() if "model loaded" not in x]}
    # 3) a model folder the library cannot load
    c = ctx.addon(args=NO_UPDATE, files={"IT/model/model.bin": "x"}, stub_ct2={})
    c.start()
    c.wait_log("could not prepare")
    c.send(1, 1, "Waiting forever")
    c.send(2, 2, "")
    c.wait_answers([2], 10)
    c.settle(1.0)
    c.close()
    out["broken_model"] = {"answers": c.answers(), "log": c.log_lines()}
    return out

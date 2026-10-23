"""First start without a model: the zip and its .sha256 come from the GitHub release of the
language; checksum mismatch, unsafe paths, offline; leftovers of an interrupted install."""

MODEL = {"model.bin": b"bin", "source.spm": b"s", "target.spm": b"t", "config.json": b"{}"}
ARGS = ["--no-update", "--editor-port", "0"]


def attempt(ctx, asset, files, sha=None, folder="local_translator_it", lang_dir="IT",
            routes=True, extra_files=None):
    gh = ctx.github()
    if routes:
        gh.model(asset, files, sha)
    a = ctx.addon(folder=folder, args=ARGS, routes=gh.routes, stub_ct2={},
                  files=extra_files or {}, cache_key="it" if lang_dir == "IT" else "pt-BR")
    a.start()
    a.wait_handshake()
    a.wait_log("model loaded|could not prepare", 30)
    a.send(1, 1, "Hello there")
    a.wait_answers([1], 5)
    a.settle(0.5)
    a.close()
    return {"log": a.log_lines(), "files": a.files(lang_dir), "answers": a.answers(),
            "net": a.net_requests(), "ct2_init": [c for c in a.ct2_calls() if "init" in c]}


def run(ctx):
    return {
        "ok": attempt(ctx, "model-it-v1/opus-mt-en-it-ct2.zip", MODEL),
        "pt_br": attempt(ctx, "model-pt-br-v1/opus-mt-en-pt-br-ct2.zip", MODEL,
                         folder="local_translator_pt_br", lang_dir="PT_BR"),
        "bad_sha": attempt(ctx, "model-it-v1/opus-mt-en-it-ct2.zip", MODEL, sha="0" * 64),
        "unsafe": attempt(ctx, "model-it-v1/opus-mt-en-it-ct2.zip", {**MODEL, "../evil.txt": b"x"}),
        "offline": attempt(ctx, "model-it-v1/opus-mt-en-it-ct2.zip", MODEL, routes=False),
        "leftovers": attempt(ctx, "model-it-v1/opus-mt-en-it-ct2.zip", MODEL,
                             extra_files={"IT/model.tmp/old.bin": "old", "IT/model/partial.txt": "x",
                                          "IT/model.zip.part": "half"}),
    }

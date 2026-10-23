"""Start and end: handshake, start log, files created, exit when the addon closes stdin;
the language from the folder name or --lang; unknown language; unknown arguments."""
import struct

FAKE = ["--fake", "--no-update", "--editor-port", "0"]


def started(ctx, **kw):
    a = ctx.addon(**kw).start()
    hs = a.wait_handshake()
    a.wait_log("TEST MODE")
    a.settle(0.5)
    code = a.close()
    return {"handshake": None if hs is None else list(struct.unpack_from("<IiIH", hs)) + [hs[14:].decode()],
            "exit": code, "log": a.log_lines(), "files": a.files(), "answers": a.answers(),
            "file_log": a.file_log()}


def unknown_language(ctx, **kw):
    a = ctx.addon(**kw).start()
    hs = a.wait_handshake(15)
    try:
        code = a.proc.wait(15)
    except __import__("subprocess").TimeoutExpired:  # a rebuild still in progress
        a.proc.kill()
        code = "did not exit"
    return {"handshake": hs is not None, "exit": code, "stderr": a.stderr, "files": a.files(),
            "log": a.file_text("local_translator.log")}


def run(ctx):
    return {
        "it_folder": started(ctx, args=FAKE),
        "pt_br_folder": started(ctx, folder="local_translator_pt_br", args=FAKE, cache_key="pt-BR"),
        "lang_arg": started(ctx, folder="anything", args=["--lang", "PT_BR", *FAKE]),
        "other_folder_from_sources": started(ctx, folder="mymodule", args=FAKE),
        "unknown_args": started(ctx, args=[*FAKE, "--future-option", "x"]),
        "debug": started(ctx, args=[*FAKE, "--debug"]),
        "fake_file": started(ctx, args=["--no-update", "--editor-port", "0"],
                             files={"IT/fake.txt": ""}),
        "unknown_lang_arg": unknown_language(ctx, args=["--lang", "xx", *FAKE]),
        "unknown_lang_folder": unknown_language(ctx, folder="local_translator_xx", args=FAKE),
    }

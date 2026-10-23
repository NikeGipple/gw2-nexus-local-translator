"""The periodic "stats" line (--stats-every, at least 10 s; times and CPU normalized), the
"N texts answered" line, answers too long for the protocol, messages too big, log lines with
newlines, and the end of a session (map merged into one file; or killed without closing)."""
import re

from addon import text_message

STUB_FILES = {"IT/model/model.bin": "x", "IT/model/source.spm": "x", "IT/model/target.spm": "x"}


def norm_stats(line):
    line = re.sub(r"^i: stats \d+ (s|min):", "i: stats <period>:", line)
    line = re.sub(r"cpu \d+% of one core \(\d+ cores\)", "cpu <n>% of one core (<n> cores)", line)
    line = re.sub(r"\d+\.\d+ lines/s", "<r> lines/s", line)
    line = re.sub(r"\(oldest \d+ s\)", "(oldest <t> s)", line)
    line = re.sub(r"sent \d+ groups", "sent <n> groups", line)
    return line


def run(ctx):
    out = {}
    patch = __import__("json").dumps({"version": 1, "strings": {"5": "Cinque"}, "glossary": "x"})
    script = {"outputs": {"Way too long": "è" * 40000}, "load_delay": 2}
    a = ctx.addon(args=["--no-update", "--editor-port", "0", "--stats-every", "3"],
                  files={**STUB_FILES, "IT/patch_it.json": patch}, stub_ct2=script)
    a.start()
    a.wait_handshake()
    msgs = [text_message(k, k, f"Line {k}") for k in range(1000, 2001)]
    msgs += [text_message(1, 1, ""), text_message(2, 2, "Waypoint"), text_message(5, 5, "Five"),
             text_message(6, 6, "Way too long"), text_message(7, 7, "Line 1000")]
    a.send_many(msgs)
    a.wait_answers([1, 2, 5, 6, 7, *range(1000, 2001)], 60)
    a.wait_log("i: stats ", 20)
    a.close()
    stats = [norm_stats(a.norm(x)) for x in a.stderr if x.startswith("i: stats ")]
    out["stats"] = {"first": stats[:1], "answers_6_7": [a.answers()["6"], a.answers()["7"]],
                    "log": [x for x in a.log_lines() if "texts answered" in x or "translated" in x
                            or "too long" in x],
                    "files": a.files("IT")}
    # a message bigger than the protocol allows: the reader stops, the module exits
    b = ctx.addon(args=["--fake", "--no-update", "--editor-port", "0"])
    b.start()
    b.wait_handshake()
    b.wait_log("TEST MODE")
    b.settle(0.5)
    b.proc.stdin.write((0x10_0001).to_bytes(4, "little"))
    b.proc.stdin.flush()
    code = b.proc.wait(20)
    b.wait_for(lambda: False, 0.3)
    out["too_big"] = {"exit": code, "log": b.log_lines(), "files": b.files("IT")}
    # killed by the addon: the map keeps its -wal file, merged at the next start
    c = ctx.addon(args=["--fake", "--no-update", "--editor-port", "0"])
    c.start()
    c.wait_handshake()
    c.send(1, 1, "Hello\r\nthere")
    c.wait_answers([1], 10)
    c.settle(1.0)
    c.kill()
    out["killed"] = {"files": c.files("IT"), "answers": c.answers()}
    c.start()
    c.wait_handshake()
    c.wait_log("map: ", 10)
    c.settle(0.5)
    out["after_kill"] = {"files": c.files("IT"), "log": c.log_lines()}
    c.close()
    out["closed"] = {"files": c.files("IT"),
                     "journal": c.db("IT/map_it.db", "PRAGMA journal_mode"),
                     "map": c.db("IT/map_it.db", "SELECT key, en, it, how FROM texts")}
    return out

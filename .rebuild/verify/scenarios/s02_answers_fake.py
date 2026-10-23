"""Answers in test mode (--fake: "[IT] text"), which still runs the whole line pipeline:
empty/trivial texts, lines with nothing to translate, glossary (exact, terms, patterns, case
variants, markup around), plural names, recipes, pieces, feminine, repeated keys, unknown message
kinds; then what is saved (map, cache, snapshot) and a second start that answers from the cache."""
import struct

FAKE = ["--fake", "--no-update", "--editor-port", "0"]

GLOSSARY = {
    "_comment": "test",
    "exact": {"Waypoint": "Waypoint", "Leggings": "Gambali", "Use All": "Usa tutto"},
    "terms": {"Lion's Arch": "Arco del Leone", "Divinity's Reach": "Divinity's Reach",
              "Large Bone": "Osso grande", "Plaza of Dwayna": "Piazza di Dwayna",
              "Might": "Might", "Raid God": "Dio dei raid", "Convergence: Mount Balrior":
              "Convergenza: Monte Balrior", "Dragon Slash—Boost": "Dragon Slash—Boost"},
    "patterns": {"{X} Longbow": "Arco lungo {X}", "{X} Short Bow": "Arco corto {X}"},
    "feminine": {"Cittadino": "Cittadina", "Mercante": "Mercante"},
}

TEXTS = [
    "", "   ", "((123))", "(new string)", "Hello world", "Hello world",  # same text, two keys
    "First line\n\nThird line", "%str1%%str2%", "<c=@flavor></c>", "[null]", "x", "12345",
    "\x8e6]2T\x93garbled", "Waypoint", "<c=@reminder>Waypoint</c>", "%str1%Leggings%str2%",
    "Pact Longbow", "<c=@reminder>Strong Bandit Short Bow</c>", "Welcome to Lion's Arch!",
    "Visit Lion's Arch and Divinity's Reach.", "raid god", "Raid god of Lion's Arch",
    "Large Bone[s]", "Plaza of<br>Dwayna", "You might use Might.", "Edible Mushroom[s]",
    "Recovered Tool Box[pl:\"Boxes\"]", "Win %num2% rated arena game[s]", "Recipe: Pact Longbow",
    "Recipe[s]: Large Bone[s]", "Recipes: Strong Bandit Short Bow", "Storyteller: Abaddon",
    "Go now. Run fast! Why? Because.", "Guild Wars 2: End of Dragons", "World vs. World",
    "Convergence: Mount Balrior", "Dragon Slash—Boost", "Title — subtitle", "Line<br>Other line",
    "<c=@flavor>Some flavor text.</c>", "%str1% evades your %str3%.", "Use All",
    "Gives 5% more power", "Gives 5%% more power", "Damage %num1%%%", "Cittadino",
    "Cittadino pettegolo di Arco", "Ladro/a", "Mr. Smith goes home. He sleeps.",
    "[f:\"Goddess\"]God", "Season Twenty-Five", "Tide Turner",
]


def ask(a, keys_texts, base_hash=1000):
    for key, text in keys_texts:
        a.send(key, base_hash + key, text)
    a.wait_answers([k for k, _ in keys_texts], 60)


def run(ctx):
    files = {"IT/glossary_it.json": __import__("json").dumps(GLOSSARY, ensure_ascii=False)}
    a = ctx.addon(args=FAKE, files=files).start()
    a.wait_handshake()
    a.send_raw(bytes([7]) + struct.pack("<I", 1))  # unknown kind: ignored
    a.send_raw(b"\x00\x01")                          # Text too short: ignored
    ask(a, list(enumerate(TEXTS, start=1)))
    # same key again with a new text, twice in one write: one registration replaces the other
    again = __import__("addon").text_message(5, 99, "Hello world again")
    a.send_many([again, again])
    a.wait_for(lambda: len(a.messages) >= len(TEXTS) + 1, 30)
    a.settle(1.0)
    a.close()
    answers = a.answers()
    answers["5"] = [x for i, x in enumerate(answers["5"]) if i == 0 or x != answers["5"][i - 1]]
    first = {"answers": answers, "log": a.log_lines(), "files": a.files(),
             "map": a.db("IT/map_it.db", "SELECT key, en, it, how FROM texts"),
             "cache": sorted(a.jsonl("IT/cache_it.jsonl"), key=repr),
             "snapshot": a.file_text("IT/cache_it.glossary.json"),
             "cache_db": a.cache_rows()}
    # second start, same folder: every line from the cache, nothing translated again
    a.start()
    a.wait_handshake()
    ask(a, [(101, "Hello world"), (102, "Pact Longbow"), (103, "Welcome to Lion's Arch!"),
            (104, "Brand new text")])
    a.settle(1.0)
    a.close()
    second = {"answers": a.answers(), "log": a.log_lines(),
              "cache": sorted(a.jsonl("IT/cache_it.jsonl"), key=repr),
              "map_count": a.db("IT/map_it.db", "SELECT count(*), sum(how = 'live') FROM texts")}
    return {"first": first, "second": second}

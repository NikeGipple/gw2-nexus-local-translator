"""Engine as the developer tools use it (lt_server.Engine with any object that has translate()):
_protect, _translate_lines, _translate_plain, outdated, _needs_translation, for Italian and
Brazilian Portuguese, with a scripted model. The calls the model receives are recorded too."""
from _common import attempt, emit

import lt_server as L

GLOSSARY = {
    "exact": {"Waypoint": "Waypoint", "Use All": "Usa tutto"},
    "terms": {"Lion's Arch": "Arco del Leone", "Large Bone": "Osso grande", "Might": "Might",
              "Raid God": "Dio dei raid", "Plaza of Dwayna": "Piazza di Dwayna",
              "Dragon Slash—Boost": "Dragon Slash—Boost"},
    "patterns": {"{X} Longbow": "Arco lungo {X}"},
    "feminine": {"Cittadino": "Cittadina"},
}

SCRIPT = {
    "Hit QZ0QZ now": "Colpisci ora",                       # placeholder lost
    "Damage +5%% Fury": "Danno +5% Furia",                  # "%%" lost
    "Nothing here": "",                                     # empty output
    "Dragon": "Drago.",                                     # final full stop added
    "Wait": "Aspetta...",
    "Gives 5% power": "Dà il 5% di potere",                 # lone %
    "Gives 5% power to QZ0QZ": "Dà il 5% di potere a QZ0QZ",
    "Thief": "Ladro/a",
    "The thief": "il/la ladro/a",
    "Citizen": "Cittadino pettegolo",
    "Gossip citizen of the city": "Cittadino pettegolo della città",
    "Old citizen, friend": "Cittadino vecchio, amico",
    "Embroidered Medal": "Medaglia ricamata",
    "Embroidered Medals": "Medaglie ricamate",
    "Shiny Helm": "un Elmo lucido",                          # rules not sure: singular kept
    "Steel Plate": "La Piastra d'acciaio",                   # article dropped
    "Golden Feather": "Golden Feather",                      # name kept in English
    "Storyteller": "Narratore",
    "Abaddon": "Abaddon.",
    "Tide Turner": "Tide TurnerCity in New Jersey USA",      # invented text
    "Captain": "Capitano",
    "Season Twenty-Five": "Stagione 22 Episodio 17",           # invented numbers
    "Finale": "finale",
    "Go now.": "Vai ora.",
    "Run fast!": "corri veloce!",
    "Some flavor text.": "Un testo di sapore.",
    "First": "Primo",
    "second": "Secondo",
    "QZ0QZ evades your QZ1QZ.": "QZ0QZ schiva il tuo QZ1QZ.",
    "You QZ0QZ use QZ1QZ.": "Tu QZ0QZ usi QZ1QZ.",
    "Hello world": "Ciao mondo",
    "Welcome to QZ0QZ!": "Benvenuto ad QZ0QZ!",
    "Plaza ofQZ0QZDwayna": "Piazza diQZ0QZDwayna",
    "Big Chest": "Grande forziere",
    "Win QZ0QZ rated arena games": "Vinci QZ0QZ partite classificate",
    "Kill the boss": "Uccidi il boss",
    "Long " + "word " * 120: "lungo",
}


class Model:
    def __init__(self, script):
        self.script, self.calls = script, []

    def translate(self, texts):
        self.calls.append(list(texts))
        return [self.script.get(t, "IT:" + t) for t in texts]


LINES = [
    "Hit %str1% now", "Damage +5%% Fury", "Nothing here", "Dragon", "Wait", "Gives 5% power",
    "Gives 5% power to %str1%", "Thief", "The thief", "Citizen", "Gossip citizen of the city",
    "Old citizen, friend", "Embroidered Medal[s]", "Shiny Helm[s]", "Steel Plate[s]",
    "Golden Feather[s]", "Recipe: Embroidered Medal", "Recipe[s]: Steel Plate[s]",
    "Recipe: Waypoint", "Storyteller: Abaddon", "Captain: Tide Turner", "Season Twenty-Five: Finale",
    "Go now. Run fast!", "<c=@flavor>Some flavor text.</c>", "First — second",
    "%str1% evades your %str3%.", "You might use Might.", "Hello world", "Welcome to Lion's Arch!",
    "Large Bone[s]", "Plaza of<br>Dwayna", "Big Chest[pl:\"Chests\"]",
    "Win %num1% rated arena game[s]", "Kill the boss", "Pact Longbow", "<c=@x>Pact Longbow</c>",
    "Use All", "Dragon Slash—Boost", "Raid god", "raid god", "[null]", "%str1%%str2%",
    "\x8e6]2T\x93", "Long " + "word " * 120, "Guild Wars 2: Janthir Wilds", "Mr. Fox runs. Fast.",
]


def engine_run(code: str) -> dict:
    L.set_language(code)
    g = L.Glossary()
    g.load_dict(GLOSSARY)
    model = Model(SCRIPT)
    e = L.Engine(model, g)
    out = {"BATCH": L.Engine.BATCH,
           "protect": {line: list(attempt(e._protect, line)) for line in LINES},
           "needs": {line: L.Engine._needs_translation(line) for line in LINES},
           "outdated": {line: [e.outdated(line, v) for v in range(1, 6)] for line in LINES}}
    out["lines_one_call"] = attempt(e._translate_lines, LINES)
    out["lines_one_call_model"] = model.calls
    model.calls = []
    out["lines_each"] = {line: attempt(e._translate_lines, [line]) for line in LINES}
    out["plain_each"] = {line: attempt(e._translate_plain, [line]) for line in LINES}
    out["plain_empty"] = e._translate_plain([])
    # pieces already in the cache are not asked again
    e.cache.update({"Storyteller": "Cantastorie", "Captain": "CAPITANO"})
    model.calls = []
    out["with_cache"] = e._translate_lines(["Storyteller: Abaddon", "Captain: Tide Turner"])
    out["with_cache_model"] = model.calls
    return out


emit({"it": engine_run("it"), "pt_br": engine_run("pt_br")})

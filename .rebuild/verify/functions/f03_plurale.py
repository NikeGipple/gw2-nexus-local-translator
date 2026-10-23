"""plurale_it: make_plural (module and tools) and num_markers / num_vocabulary (tools)."""
from _common import attempt, emit

import lt_server as L
import plurale_it as P

SINGULARS = [  # (Italian singular, English), optional model plural
    ("Medaglia precisa in juta", "Precise Jute Medal", None),
    ("Medaglia precisa in juta", "Precise Jute Medal", "Medaglie precise in juta"),
    ("Osso grande", "Large Bone", None), ("Uomo forte", "Strong Man", None),
    ("Uovo d'oro", "Golden Egg", None), ("Arma magica", "Magic Weapon", None),
    ("Problema antico", "Ancient Problem", None), ("Artista mistico", "Mystic Artist", None),
    ("Tintura Vino", "Wine Dye", None), ("Tintura rosa", "Pink Dye", None),
    ("Gioiello smeraldo", "Emerald Jewel", None), ("Lama del drago", "Dragon Blade", None),
    ("Lama", "Llama", None), ("Ostriche", "Oysters", None), ("Arancia", "Orange", None),
    ("Valigia", "Suitcase", None), ("Città", "City", None), ("Re", "King", None),
    ("Amico fedele", "Loyal Friend", None), ("Mago tecnico", "Technical Mage", None),
    ("un Elmo", "Helm", None), ("L'Elmo", "The Helm", None), ("il Martello", "The Hammer", None),
    ("la Spada", "The Sword", None), ("lo Scudo", "The Shield", None),
    ("Pact Arco", "Pact Bow", None), ("Fiala: grande", "Vial: Large", None),
    ("Pozione (forte)", "Potion (Strong)", None), ("Spada, scudo", "Sword, Shield", None),
    ("Pietra XYZ", "XYZ Stone", None), ("Pianta verde scuro", "Dark Green Plant", None),
    ("Bacca rossa matura", "Ripe Red Berry", "Bacche rosse mature"),
    ("Bacca rossa e matura", "Ripe Red Berry", "Bacche rosse e mature"),
    ("Fungo commestibile", "Edible Mushroom", "Funghi commestibili"),
    ("Pelle impregnata del drago", "Dragon Impregnated Leather", "Pelli impregnate del drago"),
    ("Spada", "Sword", "Spade e"), ("Gnu", "Gnu", None), ("Tè verde", "Green Tea", None),
    ("Fico secco", "Dried Fig", None), ("Bastone", "Staff", None), ("Banana", "Banana", None),
    ("<c=@x>Spada</c>", "Sword", None), ("Spada!", "Sword", None), ("", "", None),
    ("Pirata", "Pirate", None), ("Sistema", "System", None), ("Ala", "Wing", None),
    ("BIG ARMA", "Big Weapon", None), ("Mano", "Hand", None), ("Carta", "Card", None),
]

WORDS = ["Medaglia", "arma", "UOMO", "re", "città", "caffè", "bar", "Ostriche", "lunghe",
         "problema", "Artista", "magico", "fico", "mago", "Fuoco", "banca", "strega", "camicia",
         "arancia", "valigia", "occhio", "bacio", "x", "a", "123", "Lilla", "specie", "Cane"]

TRANSLATIONS = [
    "Vinci 3 partite", "Una partita vinta", "Le partite", "partita", "partita", "giocatore",
    "giocatore", "giocatori", "obiettivo", "obiettivo", "obiettivi", "ora", "ora", "ore",
    "minuto", "minuto", "giorno", "giorno", "porta", "porta", "porte", "vinto", "vinto", "vinta",
    "vinta", "difeso", "difeso", "difesa", "difesa", "Waypoint", "Linea di specializzazione",
    "Linea di specializzazione", "nemico", "nemici", "uccisione", "uccisione", "globe", "globe",
    "nemico", "evento", "evento", "pirata", "pirata", "giocatore attivo", "giocatore attivo",
]
EN_TEXTS = ["Win %num1% game[s]", "globe", "Shatterer", "Waypoint[s]"]

NUM_CASES = [
    ("Win %num2% rated arena game[s]", "Vinci %num2% partite classificate"),
    ("%num1% hour[s] and %num2% minute[s] in %num3% day[s]", "%num1% ore e %num2% minuti nell'arco di %num3% giorni"),
    ("Defeat %num1% enemy[s]", "Sconfiggi %num1% nemici"),
    ("Complete all %num1% objective[s]", "Completa tutti i %num1% obiettivi"),
    ("Kill %num1% player[s]", "Uccidi %num1% giocatori attivi della mappa"),
    ("Use %num1% Waypoint[s]", "Usa %num1% Waypoints"),
    ("Defeat %num1% Shatterer[s]", "Sconfiggi %num1% Shatterer"),
    ("Open %num1% door[s]", "Apri %num1% porte difese"),
    ("Win %num1% game[s]", "Vinci %num1% partite\nseconda riga"),
    ("Win %num1% game[s]", 'Vinci %num1% partita[pl:"partite"]'),
    ("Win %num1% game[s]", "Vinci %num1% xyzzi"),
    ("Win %num1% game[s]", "Vinci %num1%"),
    ("Win %num1% game[s] now", "Vinci %num1% partite ancora"),
    ("Spec %num1% line[s] started", "Linea di specializzazione %num1% avviate"),
    ("Start %num1% event[s]", "Avvia %num1% eventi, poi altri %num2% eventi"),
    ("Hunt %num1% pirate[s]", "Caccia %num1% pirati"),
    ("Kill %num1% foe[s] in %num2% min[s]", "Uccidi %num2% nemici in %num1% minuti"),
    ("Collect %num1% item[s]", "Raccogli %num1% 5 oggetti"),
    ("Achieve %num1% kill[s]", "Ottieni %num1% uccisioni (totali)"),
]


def run():
    voc = P.num_vocabulary(TRANSLATIONS)
    en_voc = {w.lower() for t in EN_TEXTS for w in P.ALPHA_RE.findall(t)}
    return {
        "make_plural": [list(attempt(P.make_plural, s, e, m)) for s, e, m in SINGULARS],
        "plural_word": {w: P.plural_word(w) for w in WORDS},
        "vocabulary": voc,
        "num_markers": [list(attempt(P.num_markers, e, it, voc, en_voc, L.english_singular,
                                     L.resolve_plural)) for e, it in NUM_CASES],
    }


emit(run())

"""Plurale italiano con regole, per i nomi di oggetti tradotti al singolare.
Usato dal modulo (lt_server, OPUS-MT sui PC) e dalla scelta 6 (tools\\patch_llm.py, TranslateGemma).

singolare + inglese -> testo con i marcatori del gioco:
    "Medaglia precisa in juta"  ->  'Medaglia[pl:"Medaglie"] precisa[pl:"precise"] in juta'

Si cambiano solo le prime parole (il nome e i suoi aggettivi), fino alla prima preposizione,
ai due punti, alla virgola o alla parentesi. Le parole che compaiono anche nell'inglese (nomi
propri, termini del glossario lasciati in inglese) non si toccano. Se c'e' anche la traduzione
al plurale del modello, allineata parola per parola, le parole piu' avanti che il modello ha
messo al plurale nello stesso modo delle regole (es. "impregnati") ricevono anche loro il marcatore.
"""
from __future__ import annotations

import re

SPLIT_RE = re.compile(r"(\s+)")
EDGE_RE = re.compile(r"^([^\w%<\[]*)(.*?)([^\w%>\]]*)$", re.S)
WORD_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+$")

# la parte da mettere al plurale finisce qui
STOP = {
    "di", "del", "dello", "della", "dei", "degli", "delle", "da", "dal", "dallo", "dalla", "dai",
    "dagli", "dalle", "a", "al", "allo", "alla", "ai", "agli", "alle", "in", "nel", "nello",
    "nella", "nei", "negli", "nelle", "su", "sul", "sullo", "sulla", "sui", "sugli", "sulle",
    "per", "con", "tra", "fra", "e", "ed", "o", "contro", "senza", "sotto", "sopra", "verso",
    "come", "che", "dopo", "prima", "presso",
}
ARTICLES = {"il": "i", "lo": "gli", "la": "le"}
NO_PLURAL = {"un", "uno", "una"}  # "un Elmo" non ha un plurale con un marcatore solo
IRREGULAR = {
    "uomo": "uomini", "uovo": "uova", "dio": "dei", "bue": "buoi", "ala": "ali", "arma": "armi",
    "mano": "mani", "paio": "paia", "eco": "echi", "amico": "amici", "nemico": "nemici",
    "greco": "greci", "porco": "porci", "antico": "antichi", "fico": "fichi", "mago": "maghi",
    "re": "re", "tè": "tè", "gnu": "gnu",
}
# nomi in -a maschili: plurale in -i
MASC_A = {
    "problema", "sistema", "tema", "schema", "programma", "diagramma", "emblema", "dilemma",
    "enigma", "stemma", "poema", "dramma", "clima", "pianeta", "poeta", "profeta", "pilota",
    "atleta", "teorema", "fantasma", "panorama", "aroma", "telegramma", "prisma", "magma",
    "sintoma", "idioma", "carisma", "cinema",
}
INVARIABLE = {
    "rosa", "viola", "lilla", "blu", "beige", "ocra", "mini", "maxi", "super", "extra",
    "standard", "pari", "dispari", "serie", "specie", "radio", "foto", "moto", "auto", "bici",
    "euro", "vaglia", "boa", "gorilla", "panda", "koala", "yak", "alpaca",
    # colori invariabili ("Tinte borgogna")
    "borgogna", "crema", "oliva", "senape", "salmone", "indaco", "ambra", "prugna", "lavanda",
    "malva", "avorio", "porpora", "fucsia", "cobalto", "magenta", "ciano", "corallo", "ruggine",
}
# dopo il nome sono apposizioni, non aggettivi: restano uguali ("Gioielli smeraldo", "Libri base",
# "Creature Ombra", "Tinture Vino")
APPOSITION = {
    "smeraldo", "rubino", "zaffiro", "oro", "argento", "bronzo", "rame", "ferro", "acciaio",
    "base", "ombra", "vino", "notte", "fuoco", "ghiaccio", "sole", "luna", "cielo", "mare",
    "tempesta", "drago", "fantasma", "spirito", "mini", "extra", "standard", "premium",
}


def _case(model: str, word: str) -> str:
    if model.isupper() and len(model) > 1:
        return word.upper()
    return word[:1].upper() + word[1:] if model[:1].isupper() else word


def plural_word(word: str) -> str | None:
    """Plurale di un nome o aggettivo italiano; None se non si sa."""
    w = word.lower()
    if not WORD_RE.match(word):
        return None
    if w in IRREGULAR:
        return _case(word, IRREGULAR[w])
    if (w in INVARIABLE or w[-1] in "àèéìòóùiy" or w[-1] not in "aeo" or len(w) < 2
            or w.endswith(("che", "ghe"))):  # "Ostriche": gia' plurale
        return word  # accentate, in -i, straniere in consonante: invariabili
    if w.endswith("ista"):
        return _case(word, w[:-1] + "i")  # artista -> artisti (maschile, il piu' comune)
    if w in MASC_A:
        return _case(word, w[:-1] + "i")
    if w.endswith("ico") and len(w) >= 6:
        return _case(word, w[:-2] + "ci")  # magico, mistico, tecnico
    for end, pl in (("co", "chi"), ("go", "ghi"), ("ca", "che"), ("ga", "ghe"), ("io", "i")):
        if w.endswith(end):
            return _case(word, w[: -len(end)] + pl)
    if w.endswith(("cia", "gia")):
        return _case(word, w[:-2] + "e" if w[-4] not in "aeiou" else w[:-1] + "e")  # arance, valigie
    if w.endswith("o") or w.endswith("e"):
        return _case(word, w[:-1] + "i")
    if w.endswith("a"):
        return _case(word, w[:-1] + "e")
    return None


def make_plural(sing: str, english: str, plur_model: str | None = None) -> tuple[str, str, list]:
    """Ritorna (testo con marcatori, esito, parole cambiate)."""
    eng_words = {x.lower() for x in re.findall(r"[A-Za-zÀ-ÿ]+", english)}
    toks = SPLIT_RE.split(sing.strip())
    words = [i for i, t in enumerate(toks) if not t.isspace()]
    ptoks = SPLIT_RE.split(plur_model.strip()) if plur_model else None
    aligned = ptoks is not None and len(ptoks) == len(toks)
    out, changed, doubt, head = list(toks), [], [], True
    for n, i in enumerate(words):
        lead, core, trail = EDGE_RE.match(toks[i]).groups()
        low = core.lower()
        if head and (low in STOP or lead):
            head = False  # questa parola passa al controllo con il modello qui sotto
        if head:
            if False:
                pass
            elif n == 0 and (low in NO_PLURAL or "'" in core or "’" in core):
                doubt.append(core)
                head = False
            elif low in ARTICLES:
                out[i] = f'{lead}{core}[pl:"{_case(core, ARTICLES[low])}"]{trail}'
                changed.append(f"{core}>{ARTICLES[low]}")
            elif WORD_RE.match(core) and low not in eng_words:
                pl = core if (low == "lama" and "llama" in eng_words) else plural_word(core)
                same_in_model = (aligned and changed and EDGE_RE.match(ptoks[i]).groups()[1] == core)
                if same_in_model or (changed and low in APPOSITION):
                    pass  # il modello la lascia uguale al plurale: "Tintura Vino" -> "Tinture Vino"
                elif pl is None:
                    doubt.append(core)
                elif pl != core:
                    out[i] = f'{lead}{core}[pl:"{pl}"]{trail}'
                    changed.append(f"{core}>{pl}")
            if trail and any(c in trail for c in ":,;(–—"):
                head = False
            continue
        # dopo la parte iniziale: solo se il modello conferma lo stesso plurale delle regole
        if aligned and WORD_RE.match(core) and low not in eng_words and low not in STOP:
            pcore = EDGE_RE.match(ptoks[i]).groups()[1]
            pl = plural_word(core)
            if pl and pl != core and pl.lower() == pcore.lower():
                out[i] = f'{lead}{core}[pl:"{pl}"]{trail}'
                changed.append(f"{core}>{pl} (confermato)")
    if doubt:
        esito = "dubbio: " + ", ".join(doubt)
    elif not changed:
        esito = "invariabile"
    else:
        esito = "ok"
    return "".join(out), esito, changed

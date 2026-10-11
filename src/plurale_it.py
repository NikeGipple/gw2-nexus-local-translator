"""Plurale italiano con regole, per i nomi di oggetti tradotti al singolare.
Usato dal modulo (lt_server, OPUS-MT sui PC) e dalla scelta 6 (tools\\patch_llm.py, TranslateGemma).
In fondo: num_markers per le frasi con %num% (solo scelta 6 per ora).

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


# --------------------------------------------------------------------------- frasi con %num%
# Il gioco lega ogni marcatore [pl:"..."] al %num% che lo precede (verificato in gioco 2026-10-11
# con /age: '%num1% ora[pl:"ore"] e %num2% minuto[pl:"minuti"] nell'arco di %num3% giorno[pl:"giorni"]'
# mostra "1 ora e 27 minuti nell'arco di 0 giorni"). Le frasi con %num% sono tradotte al plurale
# (il singolare inglese confonde i modelli); qui le parole subito dopo il numero (nome e aggettivi)
# ricevono il singolare con il marcatore: "Vinci %num2% partite" -> 'Vinci %num2% partita[pl:"partite"]'.
# Il singolare viene dal vocabolario delle traduzioni italiane (num_vocabulary): tra i singolari
# possibili ("giocatori" -> giocatoro/giocatore/giocatorio) si sceglie quello presente; se non ce
# n'e' nessuno o sono due, la frase resta al plurale come prima.

NUM_TOKEN_RE = re.compile(r"%num\d*%")
EN_MARK_RE = re.compile(r'(?<=\w)\[s\]|[\w\'’-]+\[pl:"([^"\[\]\n]*)"\]')
IT_MARK_RE = re.compile(r'\[pl:"[^"\[\]\n]*"\]')
ALPHA_RE = re.compile(r"[^\W\d_]+")
CLAUSE_END = set(".,;:!?()[]<>\"«»–—/")
ADVERBS = {"ancora", "già", "sempre", "solo", "soltanto", "anche", "poi", "più", "meno", "mai",
           "non", "ogni", "circa", "almeno", "quasi", "insieme", "subito", "inoltre", "totale"}
SEG_STOP = STOP | ADVERBS | {
    "durante", "mentre", "entro", "oltre", "fino", "quando", "se", "nell", "dell", "all", "dall",
    "sull", "coi", "col", "tramite", "attraverso", "lungo", "secondo", "dentro", "fuori", "il",
    "lo", "la", "l", "un", "una", "uno", "è", "sono", "ha", "hanno"}
PLURAL_DET = {"i", "gli", "le", "dei", "degli", "delle", "nei", "negli", "nelle", "ai", "agli",
              "alle", "sui", "sugli", "sulle", "dai", "dagli", "dalle", "coi", "questi", "queste",
              "quei", "quelle", "tutti", "tutte", "altri", "altre", "tuoi", "tue", "suoi", "sue",
              "vostri", "vostre", "nostri", "nostre", "loro", "alcuni", "alcune", "diversi",
              "diverse", "vari", "varie", "molti", "molte", "pochi", "poche"}
# dopo il nome: complemento al singolare ("Linea di specializzazione avviate") e poi aggettivi
TAIL_LINK = {"di", "del", "dello", "della", "dell", "da", "dal", "dallo", "dalla", "per", "in",
             "nel", "nello", "nella", "a", "al", "allo", "alla", "con", "su", "sul", "sulla",
             "senza", "il", "lo", "la", "l", "un", "uno", "una"}
ADJ_RE = re.compile(r"(?:at|it|ut|os|iv|ant|ent|al|il|ar)[ie]$")
GERUND_RE = re.compile(r"[ae]ndo$")
# plurale -> singolare per suffisso, quando il vocabolario non decide
SUFFIX = [("zioni", "zione"), ("sioni", "sione"), ("isti", "ista"), ("osi", "oso"), ("ose", "osa"),
          ("ivi", "ivo"), ("ive", "iva"), ("ati", "ato"), ("ate", "ata"), ("iti", "ito"),
          ("ite", "ita"), ("uti", "uto"), ("ute", "uta"), ("anti", "ante"), ("enti", "ente"),
          ("tori", "tore"), ("ici", "ico"), ("iche", "ica"), ("ali", "ale"), ("ili", "ile")]
REV_IRREGULAR = {v: k for k, v in IRREGULAR.items() if v != k}
SAME = {k for k, v in IRREGULAR.items() if v == k}
MIN_COUNT = 2
DOUBT = 0.3


def num_vocabulary(texts) -> dict[str, int]:
    """Parola italiana (minuscola) -> quante volte compare nelle traduzioni."""
    voc: dict[str, int] = {}
    for t in texts:
        for w in ALPHA_RE.findall(IT_MARK_RE.sub("", t).lower()):
            voc[w] = voc.get(w, 0) + 1
    return voc


def singular_candidates(w: str) -> list[str]:
    c = []
    if w.endswith("chi"):
        c.append(w[:-3] + "co")
    elif w.endswith("ghi"):
        c.append(w[:-3] + "go")
    elif w.endswith("ci"):
        c += [w[:-2] + "co", w[:-2] + "ce", w[:-2] + "cio"]
    elif w.endswith("gi"):
        c += [w[:-2] + "go", w[:-2] + "ge", w[:-2] + "gio"]
    elif w.endswith("i"):
        c += [w[:-1] + "o", w[:-1] + "e", w[:-1] + "io"]
    if w.endswith("che"):
        c.append(w[:-3] + "ca")
    elif w.endswith("ghe"):
        c.append(w[:-3] + "ga")
    elif w.endswith("ce"):
        c += [w[:-2] + "cia", w[:-2] + "ca"]
    elif w.endswith("ge"):
        c += [w[:-2] + "gia", w[:-2] + "ga"]
    elif w.endswith("e"):
        c.append(w[:-1] + "a")
    return [x for x in dict.fromkeys(c) if x != w and len(x) >= 3]


def singular_of(word: str, voc: dict[str, int], en_voc: set[str]) -> tuple[str | None, str]:
    """(singolare, "") se il plurale e' riconosciuto; (None, motivo) altrimenti."""
    w = word.lower()
    if w in REV_IRREGULAR:
        return _case(word, REV_IRREGULAR[w]), ""
    scored = []
    for cand in singular_candidates(w):
        n = voc.get(cand, 0)
        # "-e" che e' piuttosto il plurale di un "-a" ("porte" di "porta")
        if cand.endswith("e") and (voc.get(cand[:-1] + "a", 0) > n or (
                w.endswith("i") and voc.get(cand[:-1] + "a", 0) >= MIN_COUNT
                and voc.get(cand[:-1] + "o", 0) >= MIN_COUNT)):
            continue  # "vinti": "vinte" e' il plurale di "vinta", il singolare e' "vinto"
        if n >= MIN_COUNT:
            scored.append((n, cand))
    if len(scored) > 1:  # parole inglesi rimaste nei testi italiani ("globe") non contano
        it_only = [x for x in scored if x[1] not in en_voc]
        scored = it_only or scored
    if w.endswith("i") and not scored:  # nomi maschili in -a ("pirati", "problemi")
        cand = w[:-1] + "a"
        if voc.get(cand, 0) >= MIN_COUNT or cand in MASC_A:
            scored.append((voc.get(cand, 0), cand))
    if not scored:
        for end, sing in SUFFIX:
            if w.endswith(end) and len(w) > len(end) + 1:
                return _case(word, w[:-len(end)] + sing), ""
        return None, "singolare sconosciuto"
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[1][0] >= DOUBT * scored[0][0]:
        return None, f"singolare incerto ({scored[0][1]}/{scored[1][1]})"
    return _case(word, scored[0][1]), ""


def adjective(low: str, sing: str, voc: dict[str, int]) -> bool:
    """Participio o aggettivo al plurale: suffisso tipico, oppure maschile e femminile entrambi
    presenti nel vocabolario ("difeso"/"difesa", "maestro"/"maestra")."""
    if ADJ_RE.search(low):
        return True
    s = sing.lower()
    if s[-1:] in ("o", "a"):
        other = s[:-1] + ("a" if s.endswith("o") else "o")
        return voc.get(s, 0) >= MIN_COUNT and voc.get(other, 0) >= MIN_COUNT
    return False


def fixed_form(low: str) -> bool:
    """Parola che non cambia al plurale (accentata, in consonante, invariabile)."""
    return (low in INVARIABLE or low in SAME or low[-1] in "àèéìòóùy"
            or low[-1] not in "aeio")


def marker_nums(text: str, mark_re) -> list[str | None]:
    """Numero a cui e' legato ogni marcatore (il %num% piu' vicino prima), doppioni vicini tolti."""
    nums = [(m.start(), m.group(0)) for m in NUM_TOKEN_RE.finditer(text)]
    seq = []
    for m in mark_re.finditer(text):
        prev = [n for p, n in nums if p < m.start()]
        b = prev[-1] if prev else None
        if not seq or seq[-1] != b:
            seq.append(b)
    return seq


def english_plurals(english: str, sing_fn, plur_fn) -> dict[str, str]:
    """Parole inglesi con marcatore: plurale (minuscolo) -> singolare ("waypoints" -> "Waypoint")."""
    a, b = sing_fn(english).split(), plur_fn(english).split()
    out = {}
    if len(a) == len(b):
        for s, p in zip(a, b):
            s, p = s.strip(".,;:!?\"()"), p.strip(".,;:!?\"()")
            if s != p and ALPHA_RE.fullmatch(s) and ALPHA_RE.fullmatch(p):
                out[p.lower()] = s
    return out


def num_markers(english: str, it: str, voc: dict[str, int], en_voc: set[str],
                sing_fn, plur_fn) -> tuple[str, str]:
    """Traduzione al plurale di una frase inglese con %num% e marcatori -> (testo, esito).
    voc: num_vocabulary delle traduzioni; en_voc: parole dei testi inglesi; sing_fn / plur_fn:
    forma singolare / plurale della riga inglese (lt_server.english_singular / resolve_plural).
    Esito "ok" se il testo ha ricevuto i marcatori, altrimenti il motivo (testo invariato)."""
    if "\n" in it or IT_MARK_RE.search(it):
        return it, "salto: a capo o marcatori gia' presenti"
    eng_words = {x.lower() for x in ALPHA_RE.findall(EN_MARK_RE.sub("", english))}
    en_pl = english_plurals(english, sing_fn, plur_fn)
    toks = re.split(r"(\s+)", it)
    out = list(toks)
    marks, reasons = 0, []
    for i, t in enumerate(toks):
        nm = list(NUM_TOKEN_RE.finditer(t))
        if not nm or not t.endswith(nm[-1].group(0)) or i + 2 >= len(toks):
            continue
        if i >= 2 and toks[i - 2].lower() in PLURAL_DET:
            # "tutti i %num2% obiettivi": l'articolo resta plurale, il marcatore non aiuta
            reasons.append(f"'{toks[i - 2]}' prima del numero")
            continue
        head, k, ok, last_letter = [], i + 2, True, None
        while k < len(toks) and len(head) < 6:
            lead, core, trail = EDGE_RE.match(toks[k]).groups()
            low = core.lower()
            if (lead or not core or low in SEG_STOP or not WORD_RE.match(core)
                    or GERUND_RE.search(low)):
                break
            if low in en_pl:  # plurale inglese rimasto nel testo italiano ("Waypoints")
                head.append((k, lead, en_pl[low], core, trail))
                last_letter = None
            elif fixed_form(low):
                head.append((k, None, None, core, trail))
            else:
                s, why = singular_of(core, voc, en_voc)
                if s is None:
                    if low in eng_words:  # nome inglese ("Shatterer") lasciato com'e'
                        head.append((k, None, None, core, trail))
                    else:
                        ok = False
                        reasons.append(f"{core}: {why}")
                        break
                else:
                    head.append((k, lead, s, core, trail))
                    last_letter = low[-1]
            k += 2
            if trail:
                break
        marked = [h for h in head if h[2]]
        if not ok or not marked:
            continue
        tail = []
        trail0 = head[-1][4] if head else ""
        if last_letter in ("i", "e") and not any(c in CLAUSE_END for c in trail0):
            linked = False
            while k < len(toks):
                lead, core, trail = EDGE_RE.match(toks[k]).groups()
                low = core.lower()
                if lead or not core or NUM_TOKEN_RE.search(toks[k]):
                    break
                if low in TAIL_LINK:
                    linked = True
                elif re.fullmatch(r"\d+", core) or not WORD_RE.match(core):
                    pass
                elif low in PLURAL_DET or low in SEG_STOP:
                    break
                else:
                    s = None if fixed_form(low) else singular_of(core, voc, en_voc)[0]
                    if s is not None:  # parola al plurale
                        if linked and low[-1] == last_letter and adjective(low, s, voc):
                            tail.append((k, lead, s, core, trail))
                        else:
                            break
                if trail and any(c in CLAUSE_END for c in trail):
                    break
                k += 2
        for k2, lead, s, core, trail in marked + tail:
            out[k2] = f'{lead}{s}[pl:"{core}"]{trail}'
            marks += 1
    if not marks:
        return it, "; ".join(reasons) or "nessun nome dopo i numeri"
    res = "".join(out)
    if marker_nums(english, EN_MARK_RE) != marker_nums(res, IT_MARK_RE):
        return it, "marcatori legati a numeri diversi dall'inglese"
    return res, "ok"

"""Library API used by the developer tools: Glossary (load, lookup, terms order, case variants,
fingerprint, errors) and the text rules (resolve_plural, english_singular, plural_name,
fix_percent, drop_article, recipe_split), the regexes and the language table."""
import json

from _common import WORK, attempt, emit

import lt_server as L

GLOSSARIES = {
    "full": {"_comment": "x", "exact": {"Waypoint": "Waypoint", "Use All": "Usa tutto"},
             "terms": {"Lion's Arch": "Arco del Leone", "Lion's Arch Keep": "Rocca di Arco del Leone",
                       "Raid God": "Dio dei raid", "raid god": "DIO", "Might": "Might",
                       "Big Old Thing": "Cosa vecchia"},
             "patterns": {"{X} Longbow": "Arco lungo {X}", "{X} Long Longbow": "Arco lunghissimo {X}"},
             "feminine": {" Cittadino ": " Cittadina ", "Vuoto": ""}},
    "no_patterns": {"exact": {"Waypoint": "WP"}, "terms": {}},
    "only_terms": {"terms": {"A B": "C D"}},
    "reordered": {"terms": {"Big Old Thing": "Cosa vecchia", "Might": "Might", "raid god": "DIO",
                            "Raid God": "Dio dei raid", "Lion's Arch Keep": "Rocca di Arco del Leone",
                            "Lion's Arch": "Arco del Leone"},
                  "patterns": {"{X} Long Longbow": "Arco lunghissimo {X}", "{X} Longbow": "Arco lungo {X}"},
                  "exact": {"Use All": "Usa tutto", "Waypoint": "Waypoint"}, "_comment": "other"},
    "bad_pattern": {"patterns": {"Longbow": "Arco"}},
    "bad_section": {"terms": ["x"]},
    "numbers": {"exact": {"1": 2}, "terms": {"3": 4}},
}

LOOKUPS = ["Waypoint", "<c=@x>Waypoint</c>", "%str1%Waypoint%str2%", "Pact Longbow",
           "<c=@reminder>Strong Bandit Longbow</c>", "Hunter's Long Longbow", "pact Longbow",
           "Pact Longbow!", "Use All", "Lion's Arch", "1", "<br>Waypoint", "Waypoint<br>"]


def glossary_state(data):
    g = L.Glossary()
    r = attempt(g.load_dict, data)
    return {"load": r, "exact": g.exact, "terms": g.terms, "patterns": len(g.patterns),
            "feminine": g.feminine, "fingerprint": g.fingerprint, "version": g.version,
            "fingerprint_of": attempt(L.Glossary.fingerprint_of, data),
            "lookup": {x: g.lookup(x) for x in LOOKUPS}}


def files():
    out = {}
    g = L.Glossary()
    for name, body in {"ok": json.dumps(GLOSSARIES["full"]), "broken": "{not json",
                       "bad": json.dumps(GLOSSARIES["bad_pattern"])}.items():
        p = WORK / f"{name}.json"
        p.write_text(body, encoding="utf-8")
        out[name] = [g.load_file(p), g.fingerprint, g.version]
    out["missing"] = [g.load_file(WORK / "nope.json"), g.fingerprint, g.version]
    return out


RULE_LINES = ["Piece[s] of Gear", "Recovered Tool Box[pl:\"Boxes\"]", "Chicken[s]",
              "Win %num2% rated arena game[s]", "No markers", "Large Bone[s] and Box[pl:\"Boxes\"]",
              "[s]alone", "Lady's Fan[pl:\"Fans\"]", "Edible Mushroom[s]\nSecond[s]"]
PERCENT = [("aumenta del 5% la forza", None), ("aumenta del 5% la forza", "Gives 5% power"),
           ("aumenta del 5% la forza", "Gives power"), ("5%% e %num1% e 3%", "x % y"),
           ("%str1%%str2% 7%", "x%"), ("", "x%"), ("nessuno", "x%"), ("rotto 5%", "\x01 5%"),
           ("%num1%%%", "%num1%%%")]
ARTICLES = [("L'Insignia del mago", "Mage's Insignia"), ("Una Pietra", "A Stone"),
            ("Una Pietra", "Stone"), ("il Martello", "Hammer"), ("lo ", "X"),
            ("<c=@x>La Spada</c>", "<c=@x>Sword</c>"), ("Gli Elmi", "The Helms"),
            ("Un'ascia", "Axe"), ("Due\nrighe", "Two"), ("", "X")]
RECIPES = ["Recipe: Pact Longbow", "Recipe[s]: Large Bone[s]", "Recipes: Things",
           "<c=@x>Recipe: Pact Longbow</c>", "Recipe:Nope", "Recipe: ", "My Recipe: X",
           "Recipe: A\nB", "Recipe[s]: X"]


def rules(code):
    L.set_language(code)
    return {
        "lang": [L.LANG.code, L.LANG.cache_key, L.LANG.label, L.LANG.data_dir, L.LANG.model,
                 L.LANG.recipe, L.LANG.plural, L.LANG.file("glossary", ".json")],
        "plural_rules": L.PLURAL_RULES is not None,
        "resolve": {x: L.resolve_plural(x) for x in RULE_LINES},
        "singular": {x: L.english_singular(x) for x in RULE_LINES},
        "plural_name": {x: L.plural_name(x) for x in RULE_LINES},
        "percent": [L.fix_percent(t, e) for t, e in PERCENT] + [L.fix_percent("a 5% b")],
        "article": [L.drop_article(t, e) for t, e in ARTICLES],
        "recipe": {x: L.recipe_split(x) for x in RECIPES},
        "token_re": [m.group(0) for m in L.TOKEN_RE.finditer(
            '%str1%<c=@x>[pl:"Foci"]</c><br>[s][the][f:"Goddess"]%num12% <> [] %x% <a b>')],
        "plural_re": [m.group(0) for m in L.PLURAL_RE.finditer(' '.join(RULE_LINES))],
        "it_pl_re": [m.group(0) for m in L.IT_PL_RE.finditer('a[pl:"b"] c[pl:"d"] [pl:""]')],
        "num_re": [m.group(0) for m in L.NUM_RE.finditer("%num% %num1% %num22% %str1%")],
    }


emit({"glossaries": {k: glossary_state(v) for k, v in GLOSSARIES.items()},
      "files": files(),
      "case_variants": L.Glossary.case_variants({"Raid God": "Dio Dei Raid", "raid god": "x",
                                                  "Might": "M", "Big  Thing": "Y", "ALL CAPS": "Z"}),
      "languages": sorted(L.LANGUAGES), "protect_version": L.PROTECT_VERSION,
      "it": rules("it"), "pt_br": rules("pt_br"),
      "unknown_language": attempt(L.set_language, "xx")})

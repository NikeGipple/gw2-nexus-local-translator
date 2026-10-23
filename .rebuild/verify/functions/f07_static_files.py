"""Files that are data or configuration: the bundled default glossary (data, same bytes), the
Text Translator module descriptions (same TOML values), requirements.txt (same requirements) and
the repository's .gitignore (same patterns, in order)."""
import tomllib

from _common import PROGRAM, emit


def toml(rel):
    path = PROGRAM / rel
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def lines(path, comment="#"):
    if not path.is_file():
        return None
    return [x.strip() for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip() and not x.strip().startswith(comment)]


emit({
    "glossary_default": (PROGRAM / "glossary_it.default.json").read_bytes().decode("utf-8"),
    "module_it": toml("text_translator/module.toml"),
    "settings_it": toml("text_translator/settings.toml"),
    "module_pt_br": toml("text_translator/pt_br/module.toml"),
    "settings_pt_br": toml("text_translator/pt_br/settings.toml"),
    "requirements": lines(PROGRAM / "requirements.txt"),
    "gitignore": lines(PROGRAM.parent / ".gitignore"),
    "files": sorted(str(p.relative_to(PROGRAM)) for p in PROGRAM.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts),
})

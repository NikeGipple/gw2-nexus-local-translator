"""Posts to Discord what changed in the patches and glossaries between two commits.

Usage: python translations_summary.py <before> <after> [--dry-run]
Each language has its own Discord channel: its webhook comes from the environment
variable named in LANGS (a language whose variable is empty is only printed).
Only counts are posted: no game text and no translation leaves the repository.
"""
import json
import os
import subprocess
import sys
import urllib.request

LANGS = {  # language folder: (title, environment variable with its webhook)
    "it": ("🇮🇹 Italian", "DISCORD_UPDATES_WEBHOOK_IT"),
    "pt_br": ("🇧🇷 Brazilian Portuguese", "DISCORD_UPDATES_WEBHOOK_PT_BR"),
}
GLOSSARY_SECTIONS = ("exact", "terms", "patterns")


def git_show(rev, path):
    """JSON content of a file at a commit, or None if it does not exist there."""
    try:
        out = subprocess.run(["git", "show", f"{rev}:{path}"], check=True,
                             capture_output=True).stdout
    except subprocess.CalledProcessError:
        return None
    return json.loads(out)


def patch_pieces(rev, lang):
    try:
        out = subprocess.run(["git", "ls-tree", "--name-only", f"{rev}:patch/{lang}"],
                             check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        return []
    return [n for n in out.split() if n.endswith(".json") and n != "index.json"]


def load_patch(rev, lang):
    """All the pieces of a patch merged: {'strings': {...}, 'auto': {...}}."""
    merged = {"strings": {}, "auto": {}}
    for name in patch_pieces(rev, lang):
        piece = git_show(rev, f"patch/{lang}/{name}") or {}
        for section in merged:
            merged[section].update(piece.get(section) or {})
    return merged


def diff_dict(old, new):
    added = sum(1 for k in new if k not in old)
    changed = sum(1 for k in new if k in old and old[k] != new[k])
    removed = sum(1 for k in old if k not in new)
    return added, changed, removed


def describe(label, added, changed, removed):
    parts = []
    if added:
        parts.append(f"+{added:,} new")
    if changed:
        parts.append(f"{changed:,} updated")
    if removed:
        parts.append(f"{removed:,} removed")
    return f"{label}: " + ", ".join(parts) if parts else None


def language_lines(before, after, lang):
    """The summary lines of one language, empty if nothing changed."""
    old_p, new_p = load_patch(before, lang), load_patch(after, lang)
    lines = [
        describe("Hand-made translations", *diff_dict(old_p["strings"], new_p["strings"])),
        describe("Machine translations", *diff_dict(old_p["auto"], new_p["auto"])),
    ]
    old_g = git_show(before, f"glossary/glossary_{lang}.json") or {}
    new_g = git_show(after, f"glossary/glossary_{lang}.json") or {}
    totals = [0, 0, 0]
    for section in GLOSSARY_SECTIONS:
        for i, n in enumerate(diff_dict(old_g.get(section) or {}, new_g.get(section) or {})):
            totals[i] += n
    lines.append(describe("Glossary", *totals))
    lines = [line for line in lines if line]
    if lines:
        total = len(new_p["strings"]) + len(new_p["auto"])
        lines.append(f"Texts in the patch: {total:,}")
    return lines


def post(webhook, text):
    request = urllib.request.Request(
        webhook,
        data=json.dumps({"content": text[:2000], "allowed_mentions": {"parse": []}}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "local-translator-updates"},
    )
    urllib.request.urlopen(request).read()


def main():
    before, after = sys.argv[1], sys.argv[2]
    dry = "--dry-run" in sys.argv
    for lang, (title, env) in LANGS.items():
        lines = language_lines(before, after, lang)
        if not lines:
            print(f"{lang}: no translation changed, nothing to post.")
            continue
        text = (f"## 🔄 {title} translations updated\n\n" +
                "\n".join(f"- {line}" for line in lines) +
                "\n\nNothing to do: the module checks for updates every 15 minutes "
                "and applies them by itself, even while you play.")
        print(text, end="\n\n")
        webhook = os.environ.get(env, "")
        if dry or not webhook:
            continue
        post(webhook, text)


if __name__ == "__main__":
    main()

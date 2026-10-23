"""The real Italian model (OPUS-MT, CTranslate2) with the real glossary of the reference commit,
on synthetic game-like lines. Needs ~/fixtures/model-it/model.zip (README)."""
import zipfile

from versions import reference_file

LINES = [
    "Talk to the merchant in Lion's Arch.", "Defeat the champion.", "Gives 5% more power.",
    "Recipe: Pact Longbow", "Precise Jute Medal[s]", "Large Bone[s]", "Storyteller: Abaddon",
    "<c=@flavor>An old blade, still sharp.</c>", "Win %num1% rated arena game[s]",
    "Citizen", "Gossiping Citizen", "Thief", "The dragon is awake. Run!", "Leggings",
    "Berserker's Longbow", "Your health is low.", "Open the chest to get your reward.",
    "%str1% evades your %str3%.", "Go to the Waypoint.", "Escort the caravan to Divinity's Reach.",
    "Skin Unlocked", "Double-click to consume.", "Hunter's Short Bow", "Mystic Coin[s]",
    "Map completion: 100%%", "Protect the village from the centaurs.", "Daily Login Reward",
    "Collect %num1% bandit token[s]", "Help the Lionguard.", "Use All",
]


def run(ctx):
    model_zip = ctx.fixtures / "model-it" / "model.zip"
    with zipfile.ZipFile(model_zip) as zf:
        files = {f"IT/model/{n}": zf.read(n) for n in zf.namelist()}
    files["IT/glossary_it.json"] = reference_file("glossary/glossary_it.json")
    a = ctx.addon(args=["--no-update", "--editor-port", "0", "--threads", "2"], files=files)
    a.start()
    a.wait_handshake()
    from addon import text_message
    a.send_many([text_message(i + 1, i, t) for i, t in enumerate(LINES)])
    ok = a.wait_answers(range(1, len(LINES) + 1), 300)
    a.settle(1.0)
    a.close()
    return {"all_answered": ok, "answers": a.answers(), "log": a.log_lines(),
            "cache": sorted(a.jsonl("IT/cache_it.jsonl"), key=repr)}

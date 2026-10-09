# GW2 Nexus Local Translator

A translation module for [**Text Translator**](https://github.com/ideka/text-translator-public), the [Nexus](https://raidcore.gg/Nexus) addon by [Ideka](https://github.com/ideka) that translates Guild Wars 2 in real time as you play.

This module brings the game into **Italian**; other languages are planned.

## What makes this module different

- **Local, on-the-fly translation.** Every text is translated on your own PC, as soon as the game shows it, with [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) and [CTranslate2](https://github.com/OpenNMT/CTranslate2). It runs on the CPU: no GPU, no accounts, no API keys, and the game text never leaves your computer.
- **Corrected by hand.** Machine translation alone gets game names and terms wrong. Two hand-maintained files, published in this repository, fix that and update themselves at every game start:
  - the **glossary** keeps names and terms consistent in every sentence (for example *Lion's Arch* → *Arco del Leone*, while boons and proper names stay in English);
  - the **patch** holds curated translations of texts, identified by the game's string ID: they replace the machine translation and need no translation time.
- **Ready for other languages.** Nothing is specific to Italian except the model, the glossary and the patch: the same module can be built for any language with an OPUS-MT model from English.

> **Credits:** `text_translator.dll` is Ideka's *Text Translator*, included so that one zip contains everything. 
> This project only provides the module, built on Ideka's public module protocol.

> **Status:** early testing. Expect rough edges and breaking changes.

## Requirements

- Guild Wars 2 on Windows (64-bit), **game language set to English**
- [Nexus](https://raidcore.gg/Nexus) addon loader
- About **500 MB of free RAM** while playing, on top of what the game uses. The translator runs on the CPU (2 threads by default, at below-normal priority so the game always comes first) only while new texts are being translated.
- About **250 MB of free disk space**

## Installation

1. Go to the [Releases](../../releases) page and download `Local_Translator_IT.zip` from the newest release.
2. Extract it into your **Guild Wars 2** folder. The `addons` folder inside the zip merges with the one you already have; your other addons are not touched.
3. Start the game. If needed, enable **Text Translator** from the Nexus addon list: the *Italiano (Local Translator)* module starts by itself.

On the very first start the model is downloaded and loaded, which takes a little while. Until then text stays in English. Later starts are immediate.

> **Installed v0.2.x or older?** Those versions can no longer download patch updates (the patch has grown past the size they can read) and keep using an old copy without any warning. Download the newest release and extract it over the old one, as in the installation. Updating is also recommended from v0.3 and v0.4, which download the whole patch at every update.

The texts of the curated patch are written in advance into the addon's `cache.db`: from your next game start they appear translated right away, even the ones you have never seen before.

### Smooth play: *Pause Refreshes*

Every time new translations arrive, Text Translator refreshes all the text in the game, and each refresh causes a short stutter (about one second, as the addon itself explains in its options). For this reason the zip turns on **Pause Refreshes** in Text Translator's options (*Settings* tab):

- with **Pause Refreshes on** (default): no stutters. Texts already translated are shown translated; a text seen for the first time stays in English until it appears again (for example, talking to the same NPC again), or until you press **Refresh Now**;
- with **Pause Refreshes off**: new texts appear translated a moment after they show up, but the game stutters at each refresh. Raising *Refresh Debounce Time* makes refreshes less frequent.

After installation your `addons` folder contains:

```
addons\
  text_translator.dll                 Text Translator (Ideka)
  text_translator\
    settings.toml                     addon settings
    modules\local_translator_it\
      module.toml                     module description for the addon
      Local_Translator_IT.exe         the module (Italian build)
      _internal\                      libraries used by the module
      cache.db                        translations saved by the addon
      IT\                             Italian files (one folder per language)
        model\                        translation model (downloaded automatically)
        glossary_it.json              glossary (updated automatically)
        patch_it.db                   curated translations (updated automatically)
        cache_it.jsonl                local translation cache
        map_it.db                     local map of the texts seen in game
      local_translator_it.log         module log
```

## How it works

1. Text Translator starts the module when the game loads and stops it when the game closes.
2. It sends the module every game text with its internal string ID.
3. The module answers from the curated patch if the text is there; otherwise it translates it locally with OPUS-MT, applying the glossary.
4. Text Translator saves the translations in `cache.db` and shows them in game.
5. At every start, and whenever a new patch is published, the module also writes the whole patch into `cache.db`, so Text Translator already has those translations at the next start.

Before using the patch, the module checks that the string IDs sent by Text Translator still match the texts it knows. If they do not (for example after a change in the addon), the patch translations are removed from `cache.db` and the patch stays off until the IDs match again.

The only network connections made by the module are to this repository on GitHub: the glossary and patch checks (at start and every few hours) and the one-time model download. Text Translator itself also connects to its author's server.

## Translation patch

The curated Italian translations, identified by the game's internal string ID, are in [`patch/it/`](patch/it), split in pieces by ID range so that an update only downloads the pieces that changed. `index.json` lists the pieces with their SHA-256, which the module checks before using them:

```json
{ "strings": { "272657": "Raffina per ottenere Bloodstone Brick." }, "h": { "272657": "1a2b3c4d" } }
```

`h` is a short fingerprint (the first 8 hex digits of the SHA-256) of the English text the translation was made from: if the game sends a different English text for that ID, for example after a game update, the module translates the new text instead of showing the old translation. The patch contains no English game text.

[`patch/patch_it.json`](patch/patch_it.json) holds the same translations in a single file, for modules up to v0.4.

To report a wrong translation, open an issue with the Italian text you see in game and where you saw it.

## Your local files

`cache.db`, `cache_it.jsonl` and `map_it.db` are built on your PC while you play and contain game text owned by ArenaNet. They are for your own use only: please do not share or publish them.

## Glossary

The glossary lives in [`glossary/glossary_it.json`](glossary/glossary_it.json). It has three sections:

- `exact`: whole-text overrides, used when a text must be translated exactly in one way.
- `terms`: names that must be translated (or kept) in a fixed way inside any sentence.
- `patterns`: whole-text rules where `{X}` stands for one or more capitalized words, e.g. `"{X} Longbow": "Arco lungo {X}"`.

Suggestions and corrections are welcome through issues or pull requests.

## Other languages

Each language is a separate module (`local_translator_<language>`) with its own OPUS-MT model, glossary (`glossary_<language>.json`) and patch (`patch/<language>/`). If you would like to maintain one, open an issue.

## Troubleshooting

- The game must be set to **English**: the module translates from English only.
- Open the Nexus log: lines starting with `[Text Translator] [Italiano (Local Translator)]` come from the module. The same messages are in `local_translator_it.log`.
- If texts stay in English right after the first launch, the model is probably still downloading.
- If new texts stay in English, check *Pause Refreshes* in Text Translator's options (see [Smooth play](#smooth-play-pause-refreshes)): press **Refresh Now** or turn it off.
- If the game stutters every few seconds, turn **Pause Refreshes** on.
- Advanced: the `args` line of `module.toml` accepts `--threads N` (CPU threads used by the translator, default 2) and `--stats-every SECONDS` (writes a performance line in the log every SECONDS, default 300). Example: `args = ["--threads", "1"]`.

## Source code

The source code of the module is in [`src/`](src):

- `lt_module.py`: the Text Translator module (module protocol, requests, glossary cleanup of the cache, local map);
- `patch_db.py`: the local patch archive (`patch_it.db`), its download piece by piece and its application to `cache.db`;
- `lt_server.py`: glossary, patch format, local map and the OPUS-MT engine, used by the module as a library;
- `plurale_it.py`: Italian plural rules, so item names get both forms (`Medaglia[pl:"Medaglie"]`, the game picks the right one);
- `text_translator/`: the `module.toml` and the initial `settings.toml` shipped in the zip.

To build the module yourself on Windows with Python 3.12:

```
pip install -r src/requirements.txt pyinstaller
pyinstaller --noconsole --onedir --name Local_Translator_IT --paths src --collect-all ctranslate2 --add-data "src/glossary_it.default.json;." src/lt_module.py
```

Then copy the content of `dist\Local_Translator_IT\` and `src\text_translator\module.toml` into `addons\text_translator\modules\local_translator_it\`.

## Credits

- **Ideka**, author of [*Text Translator*](https://github.com/ideka/text-translator-public) and of its [module protocol](https://github.com/ideka/modulep).
- **Helsinki-NLP**, for the [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) models, released under CC BY 4.0.
- **OpenNMT**, for [CTranslate2](https://github.com/OpenNMT/CTranslate2).
- **Raidcore**, for Nexus.

## Disclaimer

This is an unofficial community project. It is not affiliated with or endorsed by ArenaNet, NCSOFT, Raidcore, Ideka or Helsinki-NLP.

© ArenaNet LLC. All rights reserved. NCSOFT, ArenaNet, Guild Wars, Guild Wars 2, GW2, Heart of Thorns, Path of Fire, End of Dragons, Secrets of the Obscure, Janthir Wilds, Visions of Eternity, and all associated logos, designs, and composite marks are trademarks or registered trademarks of NCSOFT Corporation. All other trademarks are the property of their respective owners.

## License

The code of this project is released under the [MIT License](LICENSE). `text_translator.dll` and other third-party components and models keep their original authors' rights and licenses.

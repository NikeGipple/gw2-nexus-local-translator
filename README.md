# GW2 Nexus Local Translator

A translation module for [**Text Translator**](https://github.com/ideka/text-translator-public), the [Nexus](https://raidcore.gg/Nexus) addon by [Ideka](https://github.com/ideka) that translates Guild Wars 2 in real time as you play.

This module brings the game into languages that Guild Wars 2 does not offer:

| Language | Module | Download | Status |
|---|---|---|---|
| Italian | Italiano (Local Translator) | `Local_Translator_IT.zip` | early testing |
| Brazilian Portuguese | Português do Brasil (Local Translator) | `Local_Translator_PT_BR.zip` | first test: it works, translations not reviewed yet |

## What makes this module different

- **Local, on-the-fly translation.** Every text is translated on your own PC, as soon as the game shows it, with [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) and [CTranslate2](https://github.com/OpenNMT/CTranslate2). It runs on the CPU: no GPU, no accounts, no API keys, and the game text never leaves your computer.
- **Corrected by hand.** Machine translation alone gets game names and terms wrong. Two files per language, published in this repository, fix that and update themselves at every game start:
  - the **glossary** keeps names and terms consistent in every sentence (for example *Lion's Arch* → *Arco del Leone* in Italian, while boons and proper names stay in English);
  - the **patch** holds curated translations, prepared in advance: they replace the machine translation and appear right away.
- **One module, many languages.** The program is the same for every language: only the model, the glossary and the patch change. Any language with an OPUS-MT model from English can be added.

> **Credits:** `text_translator.dll` is Ideka's *Text Translator*, included so that one zip contains everything.
> This project only provides the module, built on Ideka's public module protocol.

> **Status:** early testing. Expect rough edges and breaking changes.

## Requirements

- Guild Wars 2 on Windows (64-bit), **game language set to English**
- [Nexus](https://raidcore.gg/Nexus) addon loader
- Text Translator **v0.2.0 or newer** (included in the zip). Module v0.7 and later do not work with Text Translator v0.1.x, and module v0.6 and older do not work with Text Translator v0.2.0
- About **500 MB of free RAM** while playing, on top of what the game uses. The translator runs on the CPU (2 threads by default, at below-normal priority so the game always comes first) only while new texts are being translated.
- About **250 MB of free disk space** for each language

## Installation

1. Go to the [Releases](../../releases) page and download the zip of your language from its newest release:
   - Italian: `Local_Translator_IT.zip`, from the release marked *Latest*;
   - Brazilian Portuguese: `Local_Translator_PT_BR.zip`, from the newest release whose tag ends in `-pt-br`.
2. Extract it into your **Guild Wars 2** folder. The `addons` folder inside the zip merges with the one you already have; your other addons are not touched.
3. Start the game. If needed, enable **Text Translator** from the Nexus addon list: the module of your language starts by itself.

On the very first start the model of your language is downloaded and loaded, which takes a little while. Until then text stays in English. Later starts are immediate.

**Changing language.** You can install more than one language: each one is a separate module. Choose the one to use in Text Translator's options, *Modules* tab. The model of a language is downloaded only the first time that language starts.

### Updating from older versions

- **v0.6 or older:** Text Translator v0.2.0 changed the module protocol and older modules stop working with it (Nexus updates Text Translator by itself). Download the newest release and extract it over the old one, as in the installation. After the update the texts translated on the fly before are checked once more, the first time you see them: nothing to do on your side.
- **v0.2.x or older:** those versions can no longer download patch updates (the patch has grown past the size they can read) and keep using an old copy without any warning. Update as above. Updating is also recommended from v0.3 and v0.4, which download the whole patch at every update.

### Smooth play: *Pause Refreshes*

Every time new translations arrive, Text Translator refreshes all the text in the game, and each refresh causes a short stutter (about one second, as the addon itself explains in its options). For this reason the zip turns on **Pause Refreshes** in Text Translator's options (*Settings* tab):

- with **Pause Refreshes on** (default): no stutters. Texts already translated are shown translated; a text seen for the first time stays in English until it appears again (for example, talking to the same NPC again), or until you press **Refresh Now**;
- with **Pause Refreshes off**: new texts appear translated a moment after they show up, but the game stutters at each refresh. Raising *Refresh Debounce Time* makes refreshes less frequent.

### Installed files

Each language has its own module folder, `local_translator_<language>`, where `<language>` is `it` or `pt_br`. For Italian:

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
      IT\                             files of the language
        model\                        translation model (downloaded automatically)
        glossary_it.json              glossary (updated automatically)
        patch_it.db                   curated translations (updated automatically)
        cache_it.jsonl                local translation cache
        map_it.db                     local map of the texts seen in game
      local_translator_it.log         module log
```

Brazilian Portuguese uses the same layout: `modules\local_translator_pt_br\`, `Local_Translator_PT_BR.exe`, `PT_BR\` and files ending in `_pt_br`.

## How it works

- Text Translator starts the module with the game and asks it to translate every text the game shows.
- The module answers from the curated patch if the text is there; otherwise it translates it on your PC with OPUS-MT, applying the glossary. Long texts are translated piece by piece (sentences, lines, the parts around a colon), so no sentence gets lost.
- Text Translator saves the translations and shows them in game. The patch is saved in advance too, so its texts appear translated even the first time you see them.
- When a game update changes an English text, the old translation is no longer used and the new text is translated again.

The only network connections made by the module are to this repository on GitHub: the glossary and patch checks (at start and every few hours) and the one-time model download. Text Translator itself also connects to its author's server.

## Translation patch

The curated translations are in `patch/<language>/` ([`patch/it/`](patch/it) for Italian, `patch/pt_br/` for Brazilian Portuguese), split in pieces so that an update only downloads the pieces that changed. The patch contains no English game text.

To report a wrong translation, open an issue with the text you see in game, the language and where you saw it.

## Glossary

Each language has its glossary in `glossary/glossary_<language>.json` ([Italian](glossary/glossary_it.json), [Brazilian Portuguese](glossary/glossary_pt_br.json)). It has three sections:

- `exact`: whole-text overrides, used when a text must be translated exactly in one way.
- `terms`: names that must be translated (or kept) in a fixed way inside any sentence.
- `patterns`: whole-text rules where `{X}` stands for one or more capitalized words, e.g. `"{X} Longbow": "Arco lungo {X}"`.

The Brazilian Portuguese glossary keeps for now every name of the game in English.

Suggestions and corrections are welcome through issues or pull requests.

## Your local files

`cache.db`, `cache_<language>.jsonl` and `map_<language>.db` are built on your PC while you play and contain game text owned by ArenaNet. They are for your own use only: please do not share or publish them.

## Troubleshooting

- The game must be set to **English**: the module translates from English only.
- Open the Nexus log: lines starting with `[Text Translator] [Italiano (Local Translator)]` (or `[Português do Brasil (Local Translator)]`) come from the module. The same messages are in `local_translator_<language>.log`, in the module folder.
- If texts stay in English right after the first launch, the model is probably still downloading.
- If new texts stay in English, check *Pause Refreshes* in Text Translator's options (see [Smooth play](#smooth-play-pause-refreshes)): press **Refresh Now** or turn it off.
- If the game stutters every few seconds, turn **Pause Refreshes** on.
- Advanced: the `args` line of `module.toml` accepts `--threads N` (CPU threads used by the translator, default 2) and `--stats-every SECONDS` (writes a performance line in the log every SECONDS, default 300). Keep the arguments already there: for example, in Brazilian Portuguese, `args = ["--lang", "pt_br", "--threads", "1"]`.

## Source code

The source code of the module is in [`src/`](src):

- `lt_module.py`: the Text Translator module (module protocol, requests, glossary cleanup of the cache, local map);
- `patch_db.py`: the local patch archive (`patch_<language>.db`), its download piece by piece and its application to `cache.db`;
- `lt_server.py`: languages, glossary, patch format, local map and the OPUS-MT engine, used by the module as a library;
- `plurale_it.py`: Italian plural rules, so item names get both forms (`Medaglia[pl:"Medaglie"]`, the game picks the right one);
- `text_translator/`: the `module.toml` and the initial `settings.toml` shipped in the zip (Italian; Brazilian Portuguese in `text_translator/pt_br/`).

The language comes from `--lang` in `module.toml` or, without it, from the name of the module folder (`local_translator_<language>`).

To build the module yourself on Windows with Python 3.12 (Italian):

```
pip install -r src/requirements.txt pyinstaller
pyinstaller --noconsole --onedir --name Local_Translator_IT --paths src --collect-all ctranslate2 --add-data "src/glossary_it.default.json;." src/lt_module.py
```

Then copy the content of `dist\Local_Translator_IT\` and `src\text_translator\module.toml` into `addons\text_translator\modules\local_translator_it\`.

For Brazilian Portuguese use `--name Local_Translator_PT_BR` without `--add-data`, then copy `dist\Local_Translator_PT_BR\` and `src\text_translator\pt_br\module.toml` into `addons\text_translator\modules\local_translator_pt_br\`.

## Credits

- **Ideka**, author of [*Text Translator*](https://github.com/ideka/text-translator-public) and of its [module protocol](https://github.com/ideka/modulep).
- **Helsinki-NLP**, for the [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) and [Tatoeba-MT](https://github.com/Helsinki-NLP/Tatoeba-Challenge) models (Italian: `opus-mt-en-it`; Brazilian Portuguese: `eng-por`), released under CC BY 4.0.
- **OpenNMT**, for [CTranslate2](https://github.com/OpenNMT/CTranslate2).
- **Raidcore**, for Nexus.

## Disclaimer

This is an unofficial community project. It is not affiliated with or endorsed by ArenaNet, NCSOFT, Raidcore, Ideka or Helsinki-NLP.

© ArenaNet LLC. All rights reserved. NCSOFT, ArenaNet, Guild Wars, Guild Wars 2, GW2, Heart of Thorns, Path of Fire, End of Dragons, Secrets of the Obscure, Janthir Wilds, Visions of Eternity, and all associated logos, designs, and composite marks are trademarks or registered trademarks of NCSOFT Corporation. All other trademarks are the property of their respective owners.

## License

The code of this project is released under the [MIT License](LICENSE). `text_translator.dll` and other third-party components and models keep their original authors' rights and licenses.

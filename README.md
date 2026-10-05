# GW2 Nexus Local Translator

A translation module for **Text Translator**, the [Nexus](https://raidcore.gg/Nexus) addon by [Ideka](https://github.com/ideka) that translates Guild Wars 2 in real time as you play.

This module brings the game into **Italian**; other languages are planned.

## What makes this module different

- **Local, on-the-fly translation.** Every text is translated on your own PC, as soon as the game shows it, with [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) and [CTranslate2](https://github.com/OpenNMT/CTranslate2). It runs on the CPU: no GPU, no accounts, no API keys, and the game text never leaves your computer.
- **Corrected by hand.** Machine translation alone gets game names and terms wrong. Two hand-maintained files, published in this repository, fix that and update themselves at every game start:
  - the **glossary** keeps names and terms consistent in every sentence (for example *Lion's Arch* → *Arco del Leone*, while boons and proper names stay in English);
  - the **patch** holds curated translations of whole texts, identified by the game's string ID: they replace the machine translation and appear instantly.
- **Ready for other languages.** Nothing is specific to Italian except the model, the glossary and the patch: the same module can be built for any language with an OPUS-MT model from English.

> **Credits:** `text_translator.dll` is Ideka's *Text Translator*, included so that one zip contains everything. 
> This project only provides the module, built on Ideka's public module protocol.

> **Status:** early testing. Expect rough edges and breaking changes.

## Requirements

- Guild Wars 2 on Windows (64-bit), **game language set to English**
- [Nexus](https://raidcore.gg/Nexus) addon loader
- About **500 MB of free RAM** while playing, on top of what the game uses. The translator runs on the CPU (up to 4 threads) only while new texts are being translated.
- About **250 MB of free disk space**

## Installation

1. Go to the [Releases](../../releases) page and download `Local_Translator_IT.zip` from the newest release.
2. Extract it into your **Guild Wars 2** folder. The `addons` folder inside the zip merges with the one you already have; your other addons are not touched.
3. Start the game. If needed, enable **Text Translator** from the Nexus addon list: the *Italiano (Local Translator)* module starts by itself.

On the very first start the model is downloaded and loaded, which takes a little while. Until then text stays in English. Later starts are immediate.

**Updating from the previous version** (`Local_Translator.dll` + `Local_Translator_Launcher.dll`): just extract the new zip. On its first start the module disables the old version (renamed to `.dll.off`, nothing is deleted) and reuses its model and data; restart the game once.

After installation your `addons` folder contains:

```
addons\
  text_translator.dll                 Text Translator (Ideka)
  text_translator\
    settings.toml                     addon settings
    modules\local_translator_it\
      module.toml                     module description for the addon
      Local_Translator_IT.exe         the Italian translation module
      _internal\                      libraries used by the module
      cache.db                        translations saved by the addon
      IT\                             Italian files (one folder per language)
        model\                        translation model (downloaded automatically)
        glossary_it.json              glossary (updated automatically)
        patch_it.json                 curated translations (updated automatically)
        cache_it.jsonl                local translation cache
        map_it.db                     local map of the texts seen in game
      local_translator_it.log         module log
```

## How it works

1. Text Translator starts the module when the game loads and stops it when the game closes.
2. It sends the module every game text with its internal string ID.
3. The module answers from the curated patch if the text is there; otherwise it translates it locally with OPUS-MT, applying the glossary.
4. Text Translator saves the translations in `cache.db` and shows them in game.

The only network connections made by the module are to this repository on GitHub: the glossary and patch checks (at start and every few hours) and the one-time model download. Text Translator itself also connects to its author's server.

## Translation patch

[`patch/patch_it.json`](patch/patch_it.json) contains curated Italian translations identified by the game's internal string ID:

```json
{ "version": 1, "strings": { "1017171": "Riconquista il runaro" } }
```

It contains no English game text. To report a wrong translation, open an issue with the Italian text you see in game and where you saw it.

## Your local files

`cache.db`, `cache_it.jsonl` and `map_it.db` are built on your PC while you play and contain game text owned by ArenaNet. They are for your own use only: please do not share or publish them.

## Glossary

The glossary lives in [`glossary/glossary_it.json`](glossary/glossary_it.json). Suggestions and corrections are welcome through issues or pull requests.

## Other languages

Each language is a separate module (`local_translator_<language>`) with its own OPUS-MT model, glossary (`glossary_<language>.json`) and patch (`patch_<language>.json`). If you would like to maintain one, open an issue.

- `exact`: whole-text overrides, used when a text must be translated exactly in one way.
- `terms`: names that must be translated (or kept) in a fixed way inside any sentence.

## Troubleshooting

- The game must be set to **English**: the module translates from English only.
- Open the Nexus log: lines starting with `[Text Translator] [Italiano (Local Translator)]` come from the module. The same messages are in `local_translator_it.log`.
- If texts stay in English right after the first launch, the model is probably still downloading.

## Source code

The source code of the module is in [`src/`](src):

- `lt_module.py`: the Text Translator module (module protocol, patch and cache handling, local map);
- `lt_server.py`: glossary, patch and the OPUS-MT engine, used by the module as a library;
- `text_translator/`: the `module.toml` and the initial `settings.toml` shipped in the zip.

To build the module yourself on Windows with Python 3.12:

```
pip install -r src/requirements.txt pyinstaller
pyinstaller --noconsole --onedir --name Local_Translator_IT --paths src --collect-all ctranslate2 --add-data "src/glossary_it.default.json;." src/lt_module.py
```

Then copy the content of `dist\Local_Translator_IT\` and `src\text_translator\module.toml` into `addons\text_translator\modules\local_translator_it\`.

## Credits

- **Ideka**, author of *Text Translator* (and of *Japanese Text*, used by the previous version of this project).
- **Helsinki-NLP**, for the [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) models, released under CC BY 4.0.
- **OpenNMT**, for [CTranslate2](https://github.com/OpenNMT/CTranslate2).
- **Raidcore**, for Nexus.

This is an unofficial community project. It is not affiliated with or endorsed by ArenaNet, NCSoft, Raidcore, Ideka or Helsinki-NLP.

## License

The code of this project is released under the [MIT License](LICENSE). `text_translator.dll` and other third-party components and models keep their original authors' rights and licenses.

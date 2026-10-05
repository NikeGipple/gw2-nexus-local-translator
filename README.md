# GW2 Nexus Local Translator

Offline, in-game translation for **Guild Wars 2**, running as a translation module for the [Nexus](https://raidcore.gg/Nexus) addon *Text Translator* by Ideka. **Italian is the first supported language**; others are planned.

Translation runs entirely on your own PC with [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) and [CTranslate2](https://github.com/OpenNMT/CTranslate2). No accounts, no API keys, and the game text never leaves your computer.

> **Based on Ideka's work:** `text_translator.dll` is the *Text Translator* addon by [Ideka](https://github.com/ideka), included unmodified. This project only provides the Italian translation module, built on Ideka's public [module protocol](https://github.com/ideka/modulep).

> **Status:** early testing. Expect rough edges and breaking changes.

## Features

- Translates in-game text locally, on the CPU (no GPU needed). Currently available: Italian.
- Starts and stops by itself together with the game: nothing to launch manually.
- Downloads its translation model automatically on first run.
- Keeps game-specific names and terms consistent through a glossary that updates itself from this repository.
- Ships curated translations (the *patch*) that update themselves from this repository: texts covered by the patch are translated instantly, the rest is translated locally.
- Built so that other languages can be added later.

## Requirements

- Guild Wars 2 on Windows (64-bit), **game language set to English**
- [Nexus](https://raidcore.gg/Nexus) addon loader
- Internet connection on first run (about 70 MB model download)

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
3. The module answers from the curated patch, from the glossary or from the local OPUS-MT model.
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

- `exact`: whole-text overrides, used when a text must be translated exactly in one way.
- `terms`: names that must be translated (or kept) in a fixed way inside any sentence.

## Troubleshooting

- The game must be set to **English**: the module translates from English only.
- Open the Nexus log: lines starting with `[Text Translator] [Italiano (Local Translator)]` come from the module. The same messages are in `local_translator_it.log`.
- If texts stay in English right after the first launch, the model is probably still downloading.

## Credits

- **Ideka**, author of *Text Translator* (and of *Japanese Text*, used by the previous version of this project).
- **Helsinki-NLP**, for the [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) models, released under CC BY 4.0.
- **OpenNMT**, for [CTranslate2](https://github.com/OpenNMT/CTranslate2).
- **Raidcore**, for Nexus.

This is an unofficial community project. It is not affiliated with or endorsed by ArenaNet, NCSoft, Raidcore, Ideka or Helsinki-NLP.

## License

The code of this project is released under the [MIT License](LICENSE). `text_translator.dll` and other third-party components and models keep their original authors' rights and licenses.

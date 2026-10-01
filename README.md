# GW2 Nexus Local Translator

Offline, in-game translation for **Guild Wars 2**, delivered as [Nexus](https://raidcore.gg/Nexus) addons. **Italian is the first supported language**; others are planned.

Translation runs entirely on your own PC with [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) and [CTranslate2](https://github.com/OpenNMT/CTranslate2). No accounts, no API keys, and the game text never leaves your computer.

> **Status:** early testing. Expect rough edges and breaking changes.

## Features

- Translates in-game text locally, on the CPU (no GPU needed). Currently available: Italian.
- Starts and stops by itself together with the game: nothing to launch manually.
- Downloads its translation model automatically on first run.
- Keeps game-specific names and terms consistent through a glossary that updates itself from this repository.
- Built so that other languages can be added later.

## Requirements

- Guild Wars 2 on Windows (64-bit)
- [Nexus](https://raidcore.gg/Nexus) addon loader
- Internet connection on first run (about 70 MB model download)

## Installation

1. Go to the [Releases](../../releases) page and download `Local_Translator_IT.zip` from the newest release whose tag starts with `v` (for example `v0.1.0`). Releases tagged `model-...` only contain translation models, which the server downloads by itself: you do not need them.
2. Extract it into your **Guild Wars 2** folder. The `addons` folder inside the zip merges with the one you already have; your other addons are not touched.
3. Start the game. If the addons are not enabled automatically, enable **Local Client** and **Local Translator Server** from the Nexus addon list.

On the very first start the model is downloaded and loaded, which takes a little while. Until then text is not translated. Later starts are immediate.

After installation your `addons` folder contains:

```
addons\
  Local_Translator.dll             the in-game addon: sends text to the local server (listed in Nexus as "Local Client")
  Local_Translator_Launcher.dll    starts and stops the translation server with the game
  Local_Translator_IT.exe          the local translation server
  Local_Translator\                created automatically
    IT\                            Italian files (one folder per language)
      model\                       model for the language (downloaded automatically)
      glossary_it.json             glossary (updated automatically)
    lt-server.log                  server log
    launcher.log                   launcher log
```

## How it works

1. The launcher addon starts `Local_Translator_IT.exe` when the game loads it, and stops it when the game closes (also if the game crashes).
2. The translation addon sends the game text to the server on `127.0.0.1:47831`, which only accepts connections from your own PC.
3. The server translates with OPUS-MT through CTranslate2, applies the glossary, and sends the result back.

The only network connections made are to this repository on GitHub: the glossary check (at start and every few hours) and the one-time model download.

## Glossary

The glossary lives in [`glossary/glossary_it.json`](glossary/glossary_it.json). Suggestions and corrections are welcome through issues or pull requests.

- `exact`: whole-text overrides, used when a text must be translated exactly in one way.
- `terms`: names that must be translated (or kept) in a fixed way inside any sentence.

## Troubleshooting

Check the logs in `addons\Local_Translator\`:

- `launcher.log` should contain `server started`. If it is missing, the launcher addon was not loaded by Nexus.
- `lt-server.log` shows the model download and any translation error.

If texts stay in English right after the first launch, the model is probably still downloading.

## Credits

- **Ideka**, author of the *Japanese Text* addon on which the translation addon is based.
- **Helsinki-NLP**, for the [OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) models, released under CC BY 4.0.
- **OpenNMT**, for [CTranslate2](https://github.com/OpenNMT/CTranslate2).
- **Raidcore**, for Nexus.

This is an unofficial community project. It is not affiliated with or endorsed by ArenaNet, NCSoft, Raidcore, Ideka or Helsinki-NLP.

## License

Released under the [MIT License](LICENSE). Third-party components and models keep their own licenses.

# Privacy notice: map upload

Notice version 1, last updated 25 October 2026. It applies to module v0.11.0 and later.

This notice covers only the optional **map upload** of the module, described in the [README](README.md#map-upload). Without `--upload` in the `args` line of `module.toml`, the module sends nothing to the project and this notice does not apply. For now the map upload is available for Italian only.

The other connections of the module go to GitHub, to download the glossary, the patch and the model. GitHub handles them under the [GitHub General Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement), and the project receives no data from them.

## Who is responsible

The maintainer of GW2 Nexus Local Translator. Contact: **privacy@kindling.nexus**.

## What is sent

For each text met in game since 5 October 2026:

- its string ID in the game (a number);
- the English text, as the game gives it to Text Translator: these are the templates of the game, with placeholders such as `%str1%` instead of names, so the names of players and characters never appear in them;
- when it was last seen (date and time).

Together with them: the language (`it`) and the version of the module. Nothing else: no translations, no account or character names, no settings, no ID of you or of your PC.

## Your IP address

Every connection over the Internet carries an IP address, and the law treats it as personal data.

- **The project's server** does not store it. For each upload it computes, only in memory, a fingerprint of the address with a random key that changes every day (UTC) and is never saved. The fingerprint is used to limit how much one address can send in a day, a protection against abuse. The address and the fingerprint are never written to disk or to any log. At the end of the day, or when the server restarts, the key is lost and the fingerprints can no longer be linked to any address.
- **Cloudflare** (Cloudflare, Inc., USA) protects the server, and every connection passes through it. Cloudflare handles the IP address under its own [privacy policy](https://www.cloudflare.com/privacypolicy/), and may keep it in its own logs, for example for requests it blocks. The project can see those blocked requests in the Cloudflare dashboard, but it does not copy or keep them.
- **DigitalOcean** (DigitalOcean, LLC, USA) hosts the server, in its Frankfurt data center (Germany), under its own [privacy policy](https://www.digitalocean.com/legal/privacy-policy).

Both companies are based in the USA and state that they take part in the EU-U.S. Data Privacy Framework for data coming from the EU.

## Why, and on what legal basis

- **Sending the texts:** your consent (GDPR art. 6(1)(a); for reading data on your PC, art. 5(3) of the ePrivacy Directive). You give it by choosing the *autosend* zip or by writing `--upload` yourself. You can withdraw it at any time: see [How to stop](README.md#map-upload). Withdrawing does not affect the uploads already sent.
- **The daily limit on the IP address:** the legitimate interest in protecting the server from abuse (GDPR art. 6(1)(f) and recital 49).
- **Purpose of the texts:** they tell the project which texts players actually meet in game, so it knows what to translate and review next, and which texts a game update has changed.

## How long

- **IP address:** never stored by the project. The fingerprint stays in memory until the end of the day (UTC) at the latest.
- **Uploads on the server:** until the maintainer downloads them; then they are deleted from the server.
- **Downloaded uploads:** merged into the project's map of texts, which is not published, and kept privately as a backup of the merges. They contain no personal data.

## Game texts

The English texts of the game belong to ArenaNet. The project uses them only, without any profit, to prepare the translations, and does not publish them: the patch published in this repository contains no English game text.

## Your rights

Under the GDPR you have the right to access, rectify and erase your personal data, and to object to its processing. The project keeps nothing that identifies you: it does not store the IP address, and an upload carries no ID. For this reason it cannot find, show or delete "your" uploads (GDPR art. 11). For the data kept by Cloudflare or DigitalOcean, refer to their own privacy policies.

For questions or requests, write to privacy@kindling.nexus. You also have the right to lodge a complaint with a data protection authority: in Italy, the [Garante per la protezione dei dati personali](https://www.garanteprivacy.it), or the authority of the country where you live.

## Minors

If you are under 14, ask a parent or guardian before turning the map upload on.

## Changes

Every change to this notice gets a new version number and date, here and in the history of this repository. A future version of the module will not send more kinds of data than those described here, unless this notice is updated first and the change is announced in the release notes.

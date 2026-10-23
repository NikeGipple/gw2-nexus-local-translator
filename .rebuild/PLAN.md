# Rebuild tracking file

Internal working file of the rebuild (Claude's notes), not part of the project.

## Reference

- Repository: github.com/NikeGipple/gw2-nexus-local-translator
- Reference: branch `main`, commit `63326cbd6270e3ccbb2e28b8722bf0a4fcf374e0` (module v0.10.0)
- Rebuild branch: `rebuild` (orphan)
- Scope: everything in `src/` (module + the `lt_server` library API used by the private dev tools in
  `_supporto/tools`), `.gitignore`. Not rebuilt: README, LICENSE text (user copies by hand),
  `glossary/` and `patch/` (published data the module downloads from `main` at run time).
- Commits: no Claude attribution lines (user's standing rule).

## Environment

- Claude's Linux workspace (cloud), Python **3.12.11** (uv `cpython-3.12.11-linux-x86_64-gnu`, same
  version as the user's `_supporto/.venv`), `ctranslate2==4.8.2`, `sentencepiece==0.2.2`,
  `xxhash==4.0.1` (versions read from the user's `_supporto/.venv/Lib/site-packages`).
  Setup: `uv venv -p 3.12.11 venv && VIRTUAL_ENV=venv uv pip install -r .rebuild/verify/requirements.txt`
- The exe is built on Windows with PyInstaller (strumenti.bat, private repo): out of scope. Windows-only
  code (`os.name == "nt"` branches) is verified with a fake OS layer (see Verification).
- Real model: GitHub release `model-it-v1/opus-mt-en-it-ct2.zip` (sha256 c0acf11b…3c94), downloaded
  into the workspace for verification, never committed.

## Analysis summary

### Files (original `src/`)
- `lt_module.py` (808) — entry point (exe). Protocol with Text Translator, Dispatcher, Stats, IdCheck,
  ModuleEngine (cache.db purge), cache_db_watcher, CLI `main()`.
- `lt_server.py` (1890) — library: Language table, text rules (plural, percent, gender, feminine,
  article, recipe, pieces), Glossary + GlossaryUpdater, JSON Patch (+PatchUpdater, dev only),
  piece format (split/stamp/index/parse), KeyMap (map_<lang>.db + suggestions), translators
  (Fake, CT2), ensure_model, Engine (worker, cache, sync_glossary, protect, translate), dev commands
  (export/import review, build_auto, Progress).
- `patch_db.py` (983) — CachePatch (patch_<lang>.db archive, lookup, apply to cache.db, withdraw,
  migrate from v0.4 JSON, update_pieces), PieceUpdater (GitHub pieces), show-keys helpers.
- `editor.py` (501) — contributor correction page: contributor.json, Editor (search/row/save/undo),
  HTTP handler on 127.0.0.1, PAGE (HTML/CSS/JS, Italian UI).
- `plurale_it.py` (383) — Italian plural rules: make_plural (module) and num_markers (dev only).
- `glossary_it.default.json` (bundled fallback, data), `requirements.txt` (unpinned), `text_translator/`
  module.toml + settings.toml for it and pt_br (Text Translator config, active_module = folder name
  as byte list).

### Boundaries
- Started by Text Translator with args from module.toml. CLI: `--lang --fake --lang-dir --cache-db
  --patch-file --no-update --branch --debug --editor-port --threads --stats-every --export-review
  --import-review --show-keys`; unknown args ignored (warning). Language: `--lang` or folder name
  `local_translator_<code>`; unknown → writes `local_translator.log`, exit 3, no handshake. Not frozen
  and no match → "it".
- Protocol modulep v2 on binary stdin/stdout: frames `u32 len + body`; handshake
  `<IiI` (2, 0, 1) + u16-string cache key; addon Text `0 | u32 id | u64 hash | u16 len | utf8`;
  module Text same layout; Cancel kind 1 (defined, never sent). Unknown kinds ignored; >1 MiB error.
  stdin EOF → close maps, exit 0 (os._exit). stdout redirected to devnull after handshake.
- stderr: Nexus log lines `e:/w:/i:/d: msg\n`, newlines → `\0`, INFO+. File log
  `local_translator_<code>.log` (Rotating 500 kB ×1) next to exe, DEBUG with --debug.
- Files in `<lang_dir>` (default `<module_dir>/IT`): glossary_it.json (+.etag), cache_it.jsonl,
  cache_it.glossary.json (snapshot), map_it.db, patch_it.db (+ patch_it.download/ temp), model/,
  fake.txt (turns on fake), show_keys.txt, contributor.json, legacy v0.4 patch_it*.json.
- cache.db (addon's): table translations(id, cache_key, src_hash, version, timestamp, text); module
  reads, deletes rows, rewrites texts (show keys); patch texts reach the game as unrequested Text msgs.
- Network (urllib): api.github.com commits/<branch> (sha), raw.githubusercontent.com glossary and
  patch pieces (pinned to sha), single-file patch fallback on 404 index, model zip + .sha256 from
  releases. UA "gw2-local-translator", gzip accepted for pieces.
- HTTP editor on 127.0.0.1:8770 (Host/Origin checks, CSP), only with contributor.json + map.
- Library API used by `_supporto/tools`: see "Library names" below (names must stay identical).

### Library names used by the private tools (keep identical)
lt_server: LANGUAGES, LANG, set_language, Glossary (load_file, lookup, terms, exact, patterns,
fingerprint…), Engine (+BATCH, _translate_lines, _needs_translation…), CT2Translator, TOKEN_RE,
PLURAL_RE, IT_PL_RE, NUM_RE, PLURAL_RULES, PROTECT_VERSION, plural_name, recipe_split, drop_article,
english_singular, resolve_plural, fix_percent, Progress, build_auto, Patch, PatchUpdater, dump_patch,
english_hash, hash_int, OUTDATED_HASH, PIECE_RE, PIECE_WIDTH, PIECE_MAX_BYTES, index_bytes,
parse_index, parse_piece, parse_piece_versions, split_patch, stamp_versions, SUGGESTIONS_SCHEMA.
patch_db: CachePatch, KIND_*, PUSH_PAUSE. plurale_it: make_plural, num_markers, num_vocabulary.
(Tool `prova_patch_pezzi.py` also uses Patch.keys/_skipped/suppress and PatchUpdater.)

### Behaviors (data flow)
1. Start: parse args → language → handshake → log → reader thread buffers requests →
   glossary (file / bundled default / none) → CachePatch (+migrate, --patch-file) → ModuleEngine
   (cache load, sync_glossary, worker) → KeyMap → Dispatcher → contributor/editor → model loader
   thread (or fake) → cache_db_watcher → GlossaryUpdater + PieceUpdater (unless --no-update).
2. Request: empty/trivial → echo; valid suggestion → answer; patch lookup (src_hash check, skip,
   ids failed) → answer; else per line: glossary lookup / cache / given up / no letters → ready,
   otherwise queue; answer when all lines ready. Answers saved in map ('live').
3. Worker: batches of 24, gen check, failures → given_up after 2, cache append, finished lines.
4. Translate: recipes, pieces, plain (protect markup+terms, model, restore checks, final period,
   fix_percent, masculine, plural markers, feminine).
5. Glossary change: sync_glossary (snapshot diff, protect version upgrades) → purge cache.db rows
   (protected keys kept, auto keys suppressed) → patch.apply.
6. Patch: pieces download by sha, update_pieces, apply to cache.db (push changed/new texts, delete
   left-auto and drop rows, applied/skip/dropped registries, suggestions win), IdCheck (50 texts vs
   map) → withdraw / ids_ok.
7. Show keys, Stats line every 300 s, editor page.

## Bugs and oddities found (ids assigned when reproduced)

- BUG-01 `Glossary.lookup`: returns None before the wrapped-core exact check when the glossary has no
  patterns (`<c=@x>Waypoint</c>` not matched with the built-in default). Seen: f02 `no_patterns`.
- BUG-02 `http_get`: the size limit (piece size from the index) is checked on the gzip-compressed
  bytes; a piece smaller than its compressed form (~<40 bytes) always fails ("file larger than N
  bytes"). Seen: s08 `tiny_gzip_piece`.
- BUG-03 `sync_key_tags`: a cache.db row with NULL text crashes it; at start the whole
  cache_db_watcher step fails ("cannot apply the patch ... NoneType"). Seen: s11 `null_text_row`.
- BUG-04 `--show-keys` on a module folder without its IT\ folder: FileNotFoundError traceback,
  exit 1. Seen: s11 `cmd_no_lang_dir`.
- BUG-05 `ensure_model`: an unsafe path in the zip leaves `model.zip.part` (and an empty
  `model.tmp`) behind. Seen: s05 `unsafe`.
- (oddity, not rebuilt: no effect) dead code: `Patch.skipped_state`, `_applied_path`/`_read_applied`,
  `_dropped_path`/`_read_dropped`; globals `GLOSSARY_URL`, `PATCH_URL` assigned, never read.
- (oddity) `plurale_it.make_plural`: `if False: pass` branch, no effect.
- (oddity) `Patch.apply` raises NotImplementedError: `PatchUpdater.apply` always logs
  "cannot apply the patch" (only reachable from the dev test tool). f04 `updater`.
- (oddity) `feminine()` also marks nouns ending in -o after a role name ("pettegolezzo" ->
  `[f:"pettegolezza"]`). s04 key 11. Heuristic, kept.
- (races in the original, kept) answers/cache.db writes vs. threads: the IDs check and its cache.db
  work, editor `shown` read right after sending (s10, s12, s13 read the settled state).

## Verification

`.rebuild/verify/` (README there). All refs recorded from the original; every check repeatable
(`run.py stable`). Levels:
- L1 recorded results with the real model: s04_real_model.
- L2 fake host (fake addon + fake network + stub model): s01-s03, s05-s15.
- L3 isolated functions of the original: f01-f05 (library API used by the tools), f06 (Windows-only
  functions with a fake kernel32/msvcrt; rebuild function names go in its FUNCTIONS table),
  f07 (data/config files; also the file list of src/, which strumenti.bat copies by name).
- L4 derived expectations: none needed so far.

## Plan

Each step: what it adds -> checks it completes (`run.py check NAME[:part]`). Done steps shrink to
`name — commit`. Same 5 module files as the original (tools and strumenti.bat use their names).

### A. The module talks to the addon (lt_module.py)
1a. handshake — done
1b. Wait until the addon closes stdin, then exit 0. -> s01_start:it_folder.exit
2. Language of the module: LANGUAGES (code, cache key, label, data dir), --lang / folder name,
   unknown -> local_translator.log + exit 3. -> s01 (handshake/exit parts)
3. Read Text messages, ignore other kinds, answer each one (first: the English text as it is).
4. Logs: Nexus stderr lines, rotating file log, start line, unknown arguments, --debug. -> s01
5. Test translator: --fake / fake.txt answers "[IT] text", line by line ("\n").
6. Translation worker: Engine queue + condition, Dispatcher (pending/missing/waiting), batches of
   24, early requests buffered, repeated key replaces the old request.
7. Texts with nothing to translate: empty/trivial, garbled data, no letters outside markup.
### B. The line pipeline (lt_server.Engine)
8. Markup protection (TOKEN_RE placeholders) and restore checks (lost placeholder, "%%").
9. After the model: final full stop, fix_percent, masculine.
10. Glossary file / built-in default / none, exact lookups, fingerprint, log line. (BUG-01 later)
11. Glossary terms inside sentences: term_regex, longest first, case variants.
12. Glossary patterns {X} and markup around a whole line (BUG-01). -> f02 glossaries
13. plurale_it.make_plural/plural_word. -> f03:make_plural, f03:plural_word
14. Plural names in the engine: resolve_plural, english_singular, plural_name, italian_plural.
15. drop_article; 16. feminine (+ glossary section); 17. recipes (recipe_split).
18. Lines in pieces: split_pieces/_piece_edges; 19. join_pieces, invented_piece, same_case.
    -> f01, f02 (rules), s02 first-session answers
### C. Cache and model
20. cache_it.jsonl load/append, second start from the cache. -> s02
21. Glossary snapshot and sync_glossary (reset, diff, exact changes). 22. Protection version
    upgrades and old snapshot formats. -> s15:protect
23. CT2Translator, model loader thread, --threads, requirements.txt. 24. Failed batches, given up lines, gen check.
    -> s03, s04
25. ensure_model download (sha256, zip safety, leftovers, BUG-05). -> s05
### D. Map
26. KeyMap texts (save 'live', count, WAL, checkpoint at start, close at exit). -> s14 killed/closed
### E. Patch
27. CachePatch archive (schema, meta), import of a local patch_it.json, lookup answers, counts.
28. --patch-file. 29. Migration of v0.4 files and re-import by file signature.
30. Older archives (_upgrade). -> s08
31. src_hash validity (stale log, outdated hash), hash_int.
32. apply: texts sent to the game (changed / new), Dispatcher.push, cache_db_watcher.
33. apply: rows removed (drop, automatic texts that left), applied/dropped registries.
34. Glossary purge of cache.db (ModuleEngine), protected keys, skip/suppress, auto_current. -> s09
35. http_get (BUG-02) + latest_commit; GlossaryUpdater (ETag, 304, branch fallback, checks). -> s06
36. Piece format parse + piece names; 37. PieceUpdater + update_pieces; 38. single-file fallback.
    -> s07, s08
39. IdCheck, withdraw, ids_ok. -> s10
40. Show keys: key_tag, sync_key_tags (BUG-03), show_keys.txt, prefixes in answers and apply;
    41. --show-keys command (BUG-04). -> s11
42. Stats line. -> s14
### F. Contributors
43. Suggestions table (+ old layout upgrade), valid_suggestion, answers from suggestions,
    learn_english. 44. Suggestions in apply. 45. contributor.json.
46. Editor search/row. 47. Editor save/undo. 48. HTTP handler + start. -> s12
49. PAGE: HTML + CSS. 50. PAGE: script. -> s13
### G. Developer library and commands
51. Single-file Patch class, dump_patch, _write_patch; export/import review (+ CLI). -> f05:review,
    s15:commands
52. Publishing format: split_patch, piece_bytes, stamp_versions, index_bytes, english_hash,
    constants. -> f04:pieces
53. Patch.keys/_skipped/suppress, PatchUpdater. -> f04:patch
54. build_auto + Progress. -> f05:build_auto, f05:progress
55. plurale_it num_markers and its vocabulary helpers (2 steps). -> f03
### H. Platform and configuration
56. Windows streams and priority. -> f06
57. text_translator/*.toml, glossary_it.default.json (copied data), .gitignore. -> f07
### Final: all checks, BUGS.md, remove .rebuild/ if the user wants.

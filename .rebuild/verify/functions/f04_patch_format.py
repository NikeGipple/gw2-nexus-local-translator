"""Library API of the published patch (tools/pubblica_pezzi.py and the module): the piece format
(split_patch, stamp_versions, index_bytes, parse_index, parse_piece, parse_piece_versions),
hashes, the single-file Patch (parse, load, to_dict, dump_patch, keys, _skipped, suppress) and
PatchUpdater.validate/apply."""
import json

from _common import WORK, attempt, emit

import lt_server as L

STRINGS = {5: "Cinque", 20001: "Ventimila e uno", 3: "Tre"}
AUTO = {5: "ignored: reviewed wins", 7: "Sette", 40000: "Quarantamila"}
DROP = {5, 8, 20002, 7}
HASHES = {5: "00000000000000aa", 7: "", 40000: "ffffffffffffffff", 99: "1234567890abcdef"}


def piece_format():
    out = {}
    pieces = L.split_patch(STRINGS, AUTO, DROP, HASHES, 20000, {5: 2, 7: 1, 99: 4})
    out["split"] = {k: v.decode() for k, v in pieces.items()}
    out["split_small"] = {k: v.decode() for k, v in L.split_patch({1: "a"}, {}, set(), {}, 1).items()}
    out["split_bad_width"] = attempt(L.split_patch, {}, {}, set(), {}, 0)
    index = L.index_bytes(3, "fp", 20000, pieces)
    out["index"] = index.decode()
    out["parse_index"] = attempt(L.parse_index, json.loads(index))
    bad = json.loads(index)
    variants = {"format": {**bad, "format": 2}, "version": {**bad, "version": -1},
                "width": {**bad, "width": 0}, "glossary": {**bad, "glossary": 1},
                "pieces": {**bad, "pieces": []}, "name": {**bad, "pieces": {"x.json": {}}},
                "sha": {**bad, "pieces": {"p0000.json": {"sha256": "x", "size": 1}}},
                "size": {**bad, "pieces": {"p0000.json": {"sha256": "a" * 64, "size": 0}}},
                "big": {**bad, "pieces": {"p0000.json": {"sha256": "a" * 64, "size": 10 ** 9}}},
                "notdict": [1]}
    out["parse_index_bad"] = {k: attempt(L.parse_index, v) for k, v in variants.items()}
    for name, body in pieces.items():
        data = json.loads(body)
        number = L.piece_number(name)
        out.setdefault("parse_piece", {})[name] = attempt(L.parse_piece, data, number, 20000)
        out.setdefault("parse_versions", {})[name] = attempt(L.parse_piece_versions, data)
    bad_pieces = {"outside": ({"strings": {"20000": "x"}}, 0), "notdict": ([], 0),
                  "hash": ({"strings": {"1": "x"}, "src_hash": {"1": "XYZ"}}, 0),
                  "hash_obj": ({"src_hash": []}, 0), "empty_text": ({"auto": {"1": " "}}, 0),
                  "key": ({"strings": {"a": "x"}}, 0), "drop": ({"drop": "1"}, 0),
                  "hash_unused": ({"strings": {"1": "x"}, "src_hash": {"2": "a" * 16}}, 0)}
    out["parse_piece_bad"] = {k: attempt(L.parse_piece, d, n, 20000) for k, (d, n) in bad_pieces.items()}
    out["parse_versions_bad"] = [attempt(L.parse_piece_versions, d) for d in
                                 ({"version": []}, {"version": {"1": -1}}, {"version": {"1": True}},
                                  {"version": {"x": 1}}, {"version": {"1": "2"}}, {})]
    old = WORK / "old"
    old.mkdir()
    (old / "index.json").write_bytes(index)
    for name, body in pieces.items():
        (old / name).write_bytes(body)
    out["stamp_same"] = attempt(L.stamp_versions, old, STRINGS, AUTO, HASHES, 9)
    out["stamp_changed"] = attempt(L.stamp_versions, old, {**STRINGS, 3: "TRE"}, {7: "Sette", 41: "n"},
                                   {**HASHES, 5: "00000000000000bb"}, 9)
    out["stamp_none"] = attempt(L.stamp_versions, None, STRINGS, AUTO, HASHES, 9)
    (old / "p0002.json").unlink()
    out["stamp_missing_piece"] = attempt(L.stamp_versions, old, STRINGS, AUTO, HASHES, 9)
    out["names"] = [L.piece_name(3), L.piece_name(12345), L.piece_number("p0003.json"),
                    L.piece_number("p12.json"), L.piece_number("x.json"), L.PATCH_FORMAT,
                    L.PIECE_WIDTH, L.PIECE_MAX_BYTES, L.OUTDATED_HASH, L.PIECE_RE.pattern,
                    L.HASH_RE.pattern]
    out["hashes"] = [L.english_hash(""), L.english_hash("Hello"), L.english_hash("Città"),
                     L.hash_int("ffffffffffffffff"), L.hash_int("7fffffffffffffff"),
                     L.hash_int("8000000000000000"), L.hash_int("0000000000000000")]
    return out


def single_file_patch():
    out = {}
    data = {"version": 4, "strings": {"1": "Uno", "2": "Due"}, "auto": {"2": "x", "3": "Tre"},
            "drop": [2, 3, 4], "glossary": "fp1"}
    out["parse"] = attempt(L.Patch.parse, data)
    out["parse_bad"] = [attempt(L.Patch.parse, d) for d in
                        ({}, {"strings": []}, {"strings": {"1": ""}}, {"strings": {"x": "a"}},
                         {"strings": {"1": 2}}, {"strings": {}, "auto": []}, {"strings": {}, "version": "x"})]
    out["parse_drop"] = [attempt(L.Patch.parse_drop, d) for d in ({"drop": [1, "2"]}, {"drop": "1"},
                                                                  {"drop": ["a"]}, {})]
    out["parse_glossary"] = [attempt(L.Patch.parse_glossary, d) for d in ({"glossary": "x"},
                                                                          {"glossary": 1}, {})]
    p = L.Patch(WORK / "patch_it.skip.json")
    p.load_dict(data)
    out["loaded"] = [p.version, p.strings, p.auto, sorted(p.drop), p.glossary_fp]
    out["to_dict"] = p.to_dict()
    out["dump"] = L.dump_patch(p.to_dict())
    g = L.Glossary()
    p.glossary = g
    out["auto_current_no"] = p.auto_current()
    out["keys_not_current"] = sorted(p.keys())
    g.fingerprint = "fp1"
    out["auto_current_yes"] = p.auto_current()
    out["keys_current"] = sorted(p.keys())
    g.fingerprint = "other"
    p.suppress({3, 1, 99})
    out["skip_file"] = (WORK / "patch_it.skip.json").read_text()
    out["skipped"] = sorted(p._skipped())
    p.glossary_fp = ""
    out["skipped_no_fp_same_version"] = sorted(p._skipped())
    p.version = 5
    out["skipped_no_fp_other_version"] = sorted(p._skipped())
    files = {"ok": json.dumps(data), "broken": "{", "invalid": json.dumps({"strings": []})}
    for name, body in files.items():
        (WORK / f"{name}.json").write_text(body, encoding="utf-8")
        q = L.Patch()
        out.setdefault("load_file", {})[name] = [q.load_file(WORK / f"{name}.json"), q.version]
    out["load_file"]["missing"] = L.Patch().load_file(WORK / "none.json")
    out["apply"] = attempt(L.Patch().apply, None)
    q = L.Patch()
    q.load_dict({"version": 5, "strings": {}})
    up = L.PatchUpdater(q, WORK, None, "https://example.invalid/patch_it.json")
    out["updater"] = [up.what, up.stem, up.path.name, up.etag_path.name,
                      attempt(up.validate, {"version": 4, "strings": {}}),
                      attempt(up.validate, {"version": 5, "strings": {}}),
                      attempt(up.apply, {"version": 6, "strings": {"1": "a"}}), q.version, q.strings]
    return out


emit({"pieces": piece_format(), "patch": single_file_patch()})

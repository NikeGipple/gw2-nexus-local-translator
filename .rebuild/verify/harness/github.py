"""Fake GitHub content for the fake network: routes for the API, raw files and release assets.

Patches are built here from plain dicts, in the published piece format (index.json + pNNNN.json),
independently of either version's code.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

REPO = "NikeGipple/gw2-nexus-local-translator"
API = f"https://api.github.com/repos/{REPO}/commits/"
RAW = f"https://raw.githubusercontent.com/{REPO}/"
RELEASES = f"https://github.com/{REPO}/releases/download/"


class FakeGitHub:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.routes: dict[str, dict] = {}
        self._n = 0

    def _file(self, body: bytes) -> str:
        self._n += 1
        path = self.folder / f"blob{self._n}"
        path.write_bytes(body)
        return str(path)

    def commit(self, branch: str, sha: str) -> None:
        self.routes[API + branch] = {"text": sha}

    def raw(self, ref: str, path: str, body: bytes | str, **route) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[RAW + f"{ref}/{path}"] = {"file": self._file(body), **route}

    def missing(self, ref: str, path: str) -> None:
        self.routes[RAW + f"{ref}/{path}"] = {"status": 404}

    def glossary(self, ref: str, data: dict | bytes, lang: str = "it", etag: str = '"g1"') -> None:
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.raw(ref, f"glossary/glossary_{lang}.json", body, etag=etag)

    def patch_pieces(self, ref: str, version: int, glossary: str, width: int,
                     entries: dict[int, dict], lang: str = "it", gzip: bool = True) -> dict:
        """entries: key -> {"kind": "strings"|"auto"|"drop", "text": ..., "src_hash": "16 hex",
        "version": n}. Returns {piece name: bytes} as published."""
        pieces: dict[int, dict] = {}
        for key in sorted(entries):
            e = entries[key]
            p = pieces.setdefault(key // width, {})
            if e["kind"] == "drop":
                p.setdefault("drop", []).append(key)
                continue
            p.setdefault(e["kind"], {})[str(key)] = e["text"]
            if e.get("src_hash"):
                p.setdefault("src_hash", {})[str(key)] = e["src_hash"]
            if e.get("version") is not None:
                p.setdefault("version", {})[str(key)] = e["version"]
        order = ("strings", "auto", "drop", "src_hash", "version")
        bodies = {}
        for n, p in sorted(pieces.items()):
            data = {s: p[s] for s in order if s in p}
            bodies[f"p{n:04d}.json"] = (json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                                        + "\n").encode("utf-8")
        index = {"format": 1, "version": version, "glossary": glossary, "width": width,
                 "pieces": {name: {"sha256": hashlib.sha256(b).hexdigest(),
                                   "entries": sum(len(json.loads(b).get(s, ()))
                                                  for s in ("strings", "auto", "drop")),
                                   "size": len(b)} for name, b in bodies.items()}}
        self.raw(ref, f"patch/{lang}/index.json", json.dumps(index, indent=1) + "\n", gzip=gzip)
        for name, body in bodies.items():
            self.raw(ref, f"patch/{lang}/{name}", body, gzip=gzip)
        return bodies

    def model(self, asset: str, files: dict[str, bytes], sha: str | None = None) -> str:
        """A model zip as a release asset (+ its .sha256). Returns the real sha256."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, body in files.items():
                zf.writestr(name, body)
        body = buf.getvalue()
        real = hashlib.sha256(body).hexdigest()
        self.routes[RELEASES + asset] = {"file": self._file(body)}
        self.routes[RELEASES + asset + ".sha256"] = {"text": f"{sha or real}  {asset.split('/')[-1]}\n"}
        return real

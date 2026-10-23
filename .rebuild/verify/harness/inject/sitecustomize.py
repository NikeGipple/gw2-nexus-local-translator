"""Loaded automatically by the module process (PYTHONPATH): the fake outside world.

LT_NET_ROUTES  JSON file: {"routes": {url: route}}; any other URL fails as if offline.
               route = {"status": 200, "file": path | "text": str, "headers": {...},
                        "etag": "...", "gzip": false, "length": n}
               With "etag", a request whose If-None-Match equals it gets 304.
LT_NET_LOG     JSONL file: one line per request (url, sorted lower-case headers).
LT_STUB_CT2    JSON file: replaces ctranslate2 and sentencepiece with a scripted translator:
               {"outputs": {source: output}, "fail": [source, ...], "load_delay": seconds}
               Other sources: output = "IT:" + source. Tokens are single characters.
LT_CT2_LOG     JSONL file: Translator() arguments and every translate_batch call.
Identical for both versions: it cannot favour either.
"""
from __future__ import annotations

import gzip as _gzip
import io
import json
import os
import sys
import threading
import time
import types
from email.message import Message

_lock = threading.Lock()


def _log(env: str, record: dict) -> None:
    path = os.environ.get(env)
    if not path:
        return
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- network
def _install_network() -> None:
    import urllib.error
    import urllib.request

    routes_path = os.environ["LT_NET_ROUTES"]

    class FakeResponse(io.BytesIO):
        def __init__(self, url: str, body: bytes, headers: Message, status: int) -> None:
            super().__init__(body)
            self.url, self.headers, self.status = url, headers, status

        def getcode(self) -> int:
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            self.close()

    def urlopen(req, data=None, timeout=None, **_kw):
        if isinstance(req, str):
            req = urllib.request.Request(req)
        url = req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        _log("LT_NET_LOG", {"url": url, "headers": headers})
        with open(routes_path, encoding="utf-8") as f:
            route = json.load(f)["routes"].get(url)
        if route is None:
            raise urllib.error.URLError("offline (fake network)")
        hdrs = Message()
        for k, v in route.get("headers", {}).items():
            hdrs[k] = v
        etag = route.get("etag")
        if etag:
            hdrs["ETag"] = etag
            if headers.get("if-none-match") == etag:
                raise urllib.error.HTTPError(url, 304, "Not Modified", hdrs, io.BytesIO(b""))
        status = route.get("status", 200)
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "Error", hdrs, io.BytesIO(b""))
        if "file" in route:
            with open(route["file"], "rb") as f:
                body = f.read()
        else:
            body = route.get("text", "").encode("utf-8")
        if route.get("gzip"):
            body = _gzip.compress(body, mtime=0)
            hdrs["Content-Encoding"] = "gzip"
        hdrs["Content-Length"] = str(route.get("length", len(body)))
        return FakeResponse(url, body, hdrs, status)

    urllib.request.urlopen = urlopen


# --------------------------------------------------------------------------- translator
def _install_stub_ct2() -> None:
    with open(os.environ["LT_STUB_CT2"], encoding="utf-8") as f:
        script = json.load(f)
    outputs, fail = script.get("outputs", {}), set(script.get("fail", []))

    class Result:
        def __init__(self, tokens: list[str]) -> None:
            self.hypotheses = [tokens]

    class Translator:
        def __init__(self, model_path, device="cpu", inter_threads=1, intra_threads=0, **kw):
            time.sleep(script.get("load_delay", 0))
            if not os.path.isfile(os.path.join(str(model_path), "model.bin")):
                raise RuntimeError(f"Unable to open file 'model.bin' in model '{model_path}'")
            _log("LT_CT2_LOG", {"init": {"device": device, "inter_threads": inter_threads,
                                         "intra_threads": intra_threads,
                                         "model": os.path.basename(str(model_path)), **kw}})

        def translate_batch(self, batch, beam_size=2, max_decoding_length=256, **kw):
            sources = ["".join(t for t in tokens if t != "</s>") for tokens in batch]
            _log("LT_CT2_LOG", {"batch": sources, "ends": [tokens[-1:] for tokens in batch],
                                "beam_size": beam_size,
                                "max_decoding_length": max_decoding_length, **kw})
            if fail & set(sources):
                raise RuntimeError("stub: scripted failure")
            return [Result(list(outputs.get(s, "IT:" + s))) for s in sources]

    class SentencePieceProcessor:
        def __init__(self, model_file=None, **kw) -> None:
            if not os.path.isfile(str(model_file)):
                raise OSError(f"Not found: {model_file}")

        def encode(self, text, out_type=str, **kw):
            return list(text)

        def decode(self, tokens):
            return "".join(tokens)

    ct2 = types.ModuleType("ctranslate2")
    ct2.Translator = Translator
    spm = types.ModuleType("sentencepiece")
    spm.SentencePieceProcessor = SentencePieceProcessor
    sys.modules["ctranslate2"] = ct2
    sys.modules["sentencepiece"] = spm


if os.environ.get("LT_NET_ROUTES"):
    _install_network()
if os.environ.get("LT_STUB_CT2"):
    _install_stub_ct2()

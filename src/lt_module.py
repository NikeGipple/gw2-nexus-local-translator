"""Local Translator: a translation module for Text Translator, Ideka's Nexus addon for Guild Wars 2.

Text Translator starts this program by itself and talks to it through stdin and stdout, with its
module protocol "modulep", version 2: https://github.com/ideka/modulep
"""
from __future__ import annotations

import struct
import sys

# The handshake values (modulep v2).
PROTOCOL_VERSION = 2
SOURCE_LANGUAGE_ENGLISH = 0  # the game must be set to English
RESULT_VERSION = 1           # raising it makes the addon ask again for every text it has cached
CACHE_KEY = "it"             # the addon keeps our translations under this name


def main() -> int:
    addon = AddonConnection(sys.stdout.buffer)
    addon.send_handshake()
    return 0


class AddonConnection:
    """The pipe to Text Translator. Every message is its length as a little-endian u32 followed
    by its body (modulep)."""

    def __init__(self, outgoing) -> None:
        self.outgoing = outgoing

    def send_handshake(self) -> None:
        """The first message: the addon waits for it before sending anything."""
        self._send(struct.pack("<IiI", PROTOCOL_VERSION, SOURCE_LANGUAGE_ENGLISH, RESULT_VERSION)
                   + protocol_string(CACHE_KEY))

    def _send(self, body: bytes) -> None:
        self.outgoing.write(struct.pack("<I", len(body)) + body)
        self.outgoing.flush()


def protocol_string(text: str) -> bytes:
    """A string as modulep encodes it: its UTF-8 length as a little-endian u16, then the bytes."""
    raw = text.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


if __name__ == "__main__":
    sys.exit(main())

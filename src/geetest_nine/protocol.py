"""Geetest v4 wire-protocol primitives.

Self-contained port of the ``w`` payload builder used by Geetest v4 captcha
endpoints. Pure functions: no HTTP, no site-specific logic.

Reference: xKiian/GeekedTest sign.py (MIT).
"""

from __future__ import annotations

import binascii
import hashlib
import json
import random
import re
import urllib.parse

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

from .tracks import gen_nine_track
from .tracks.compress import track_zip
from .crypto import gen_td_sign

# --------------------------------------------------------------------------- #
# RSA public key + ABO obfuscation constants (Geetest v4 static config)
# --------------------------------------------------------------------------- #
_RSA_N = int(
    "00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D94"
    "51F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE"
    "08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0"
    "066D59CEEFA5F2748EA80BAB81",
    16,
)
_RSA_KEY = RSA.construct((_RSA_N, 0x10001))

_ABO_KEY = "(n[5:7]+n[7:9])+.+(n[20:27])+.+(n[10:10]+n[12:12]+n[3:3]+n[7:7])"
_ABO_VAL = "n[7:14]"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _rand_uid() -> str:
    out = ""
    for _ in range(4):
        out += hex(int(65536 * (1 + random.random())))[2:].zfill(4)[-4:]
    return out


def _parse_slice(s: str) -> list[int]:
    return [int(x) for x in s.split(":")]


def _extract(part: str) -> str:
    return re.search(r"\[(.*?)\]", part).group(1)


def _parse(s: str) -> list:
    parts = s.split("+.+")
    parsed = []
    for part in parts:
        if "+" in part:
            parsed.append([_parse_slice(_extract(sub)) for sub in part.split("+")])
        else:
            parsed.append([_parse_slice(_extract(part))])
    return parsed


class _LotParser:
    def __init__(self) -> None:
        self.lot = _parse(_ABO_KEY)
        self.lot_res = _parse(_ABO_VAL)

    @staticmethod
    def _build(parsed, num: str) -> str:
        result = []
        for p in parsed:
            cur = []
            for s in p:
                start = s[0]
                end = s[1] + 1 if len(s) > 1 else start + 1
                cur.append(num[start:end])
            result.append("".join(cur))
        return ".".join(result)

    def get_dict(self, lot_number: str) -> dict:
        i = self._build(self.lot, lot_number)
        r = self._build(self.lot_res, lot_number)
        a: dict = {}
        cur = a
        parts = i.split(".")
        for idx, part in enumerate(parts):
            if idx == len(parts) - 1:
                cur[part] = r
            else:
                cur[part] = cur.get(part, {})
                cur = cur[part]
        return a


_lotParser = _LotParser()


def _pow(lot_number: str, captcha_id: str, pd: dict) -> dict:
    """Proof-of-work: brute a nonce until hash prefix matches ``pd`` requirements."""
    pow_string = (
        f"{pd['version']}|{pd['bits']}|{pd['hashfunc']}|{pd['datetime']}"
        f"|{captcha_id}|{lot_number}||"
    )
    algo = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256}[
        pd["hashfunc"]
    ]
    bits = int(pd["bits"])
    hex_zeros = bits // 4
    extra = bits % 4
    prefix = "0" * hex_zeros
    max_hex = {1: "7", 2: "3", 3: "1"}.get(extra, "")
    while True:
        h = _rand_uid()
        combined = pow_string + h
        hv = algo(combined.encode()).hexdigest()
        if hv.startswith(prefix) and (extra == 0 or hv[hex_zeros] <= max_hex):
            return {"pow_msg": combined, "pow_sign": hv}


def _encrypt_w(raw: str, pt) -> str:
    """AES-CBC(payload) + PKCS1_v1_5(session_key) → hex.

    ``pt`` is the challenge's ``pt`` field: 0/"0" means plain (URL-encoded)."""
    if not pt or pt in ("0", 0):
        return urllib.parse.quote_plus(raw)
    uid = _rand_uid()
    key = uid.encode()
    iv = b"0" * 16
    enc = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(raw.encode(), AES.block_size))
    enc_key = PKCS1_v1_5.new(_RSA_KEY).encrypt(uid.encode())
    return binascii.hexlify(enc).decode() + binascii.hexlify(enc_key).decode()


# --------------------------------------------------------------------------- #
# Public: build the ``w`` verify-request field
# --------------------------------------------------------------------------- #
def build_w(
    data: dict,
    captcha_id: str,
    userresponse: list,
    cells: list | None = None,
) -> str:
    """Encode a verify payload for Geetest v4 nine-grid.

    Args:
        data: dict returned by ``/load`` (must contain ``lot_number``,
            ``pow_detail``, optionally ``guard`` and ``pt``).
        captcha_id: static captcha id for the site.
        userresponse: list of ``[x_pct, y_pct]`` pairs (Geetest standard) or
            ``[[row, col], ...]`` 1-based cell coordinates for nine-grid.
        cells: 1-based ``(row, col)`` tuples for track generation. When set,
            a nine-click track is embedded and ``td_sign`` is added.

    Returns:
        Encrypted ``w`` string ready for the verify URL.
    """
    lot = data["lot_number"]
    pd = data["pow_detail"]
    track_payload = None
    passtime = random.randint(2500, 4000)
    if cells:
        track, pt = gen_nine_track([(row + 1, col + 1) for row, col in cells])
        passtime = pt
        track_payload = track_zip(track)
    base = {
        _ABO_KEY: _ABO_VAL,
        **_pow(lot, captcha_id, pd),
        **_lotParser.get_dict(lot),
        "biht": "1426265548",
        "device_id": "",
        "em": {"cp": 0, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1},
        "ep": "123",
        "geetest": "captcha",
        "lang": "zh",
        "lot_number": lot,
        "passtime": passtime,
        "userresponse": userresponse,
    }
    if track_payload is not None:
        base["td_sign"] = gen_td_sign(lot, track_payload)
    if data.get("guard"):
        base["gee_guard"] = {
            "roe": {
                k: "3" for k in ("auh", "aup", "cdc", "egp", "res", "rew", "sep", "snh")
            }
        }
    return _encrypt_w(json.dumps(base, separators=(",", ":")), data.get("pt", "1"))

"""Crypto helpers untuk Geetest v4 nine-grid.

Nine-grid pakai ``pt=1`` (AES-CBC + RSA), yang di-handle di ``protocol.py``.
Modul ini expose ``gen_td_sign`` untuk track-data signature.

(Encoder ``pt=0``/``pt=2`` (SM2/SM4) ada di source Bitdeer-Auto/wulu_crypto.py
kalau perlu; tidak di-include di sini biar dependency enteng — nine-grid tidak
memakai SM2/SM4.)
"""
import hashlib
import hmac


def gen_td_sign(lot_number: str, track_data: str) -> str:
    """HMAC-SHA256 signature untuk track_zip payload."""
    return hmac.new(
        lot_number.encode(),
        track_data.encode(),
        hashlib.sha256,
    ).hexdigest()

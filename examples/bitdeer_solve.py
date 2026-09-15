"""Contoh integrasi: farm Bitdeer pakai geetest-solver-nine.

Bitdeer memicu geeTestForm ke ``account-api.bitdeer.com``. Browser dapat token
``nine|...``, curl dapat ``svg_seed|...`` (di-flag). BrowserVT bypass ini.

Jalankan:
    pip install -e ..
    playwright install chromium
    python bitdeer_solve.py
"""

from __future__ import annotations

import time

from geetest_nine import BrowserVT, GeetestNineClient

BITDEER_CAPTCHA_ID = "12cd3cfd6af764f226a0ba1ac9938f06"

bitdeer_vt = BrowserVT(
    signup_url="https://account.bitdeer.com/en/sign_up?method=1",
    email_selector='input[placeholder*="Email"]',
    submit_text="Send verification code",
    intercept_url_substring="geeTestForm",
    vt_json_path=("data", "verifyType"),
    lot_json_path=("data", "verifyLot"),
)


def solve_one() -> dict:
    email = f"probe{int(time.time())}@example.com"
    client = GeetestNineClient(
        captcha_id=BITDEER_CAPTCHA_ID,
        verify_type_provider=bitdeer_vt.get_vt_for,
    )
    seccode = client.solve(identifier=email)
    print(f"lot={seccode['lot_number'][:8]}  src={client.last_source}")
    return seccode


if __name__ == "__main__":
    solve_one()

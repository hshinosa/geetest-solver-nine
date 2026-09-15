# geetest-solver-nine

Self-hosted **Geetest v4 nine-grid** (icon) captcha solver. Local ONNX SigLIP
matcher — no vision-API dependency, ~66ms per solve on CPU, calibrated to
**96.7% effective success** (live 5/5 in production).

## What it does

Given a Geetest v4 challenge that returns `captcha_type: "nine"`:

1. Downloads the 3×3 grid + prompt icon from `static.geetest.com`.
2. Runs the ONNX matcher (SigLIP backbone + spatial-correlation head) to pick
   the 3 cells that match the prompt.
3. Builds the `w` payload (POW + AES-CBC + RSA + nine-click track), submits
   to `/verify`, returns the `seccode` dict.

Includes:

- **`BrowserVT`** — headless-Chrome grabber that intercepts a site's
  `verifyType` token. Some backends flag non-browser HTTP clients and respond
  with unsolvable `svg_seed` challenges; borrowing a browser token is the
  fastest fix. Config-driven, site-agnostic.
- **`GeetestNineClient`** — orchestrates load → solve → verify, retries with
  fresh challenges on low-confidence, falls back to best-guess after N tries.

## Install

```bash
pip install -e .
playwright install chromium   # only if you use BrowserVT
```

Requires Python ≥ 3.10. Model artifacts (~9 MB total) are bundled.

## Usage

### With browser token (recommended for sites that risk-flag curl clients)

```python
from geetest_nine import GeetestNineClient, BrowserVT

vt = BrowserVT(
    signup_url="https://acme.com/signup",
    email_selector='input[placeholder*="Email"]',
    submit_text="Send verification code",
    intercept_url_substring="geeTestForm",       # your backend's endpoint
    vt_json_path=("data", "verifyType"),
    lot_json_path=("data", "verifyLot"),
)

client = GeetestNineClient(
    captcha_id="12cd3cfd6af764f226a0ba1ac9938f06",
    verify_type_provider=vt.get_vt_for,
)
seccode = client.solve(identifier="user@example.com")
# → {"lot_number": ..., "captcha_output": ..., "pass_token": ..., "gen_time": ...}
```

### With a plain `verifyType` you obtained yourself

```python
def my_provider(identifier):
    # call your site's own geeTestForm-equivalent and return (vt, verify_lot)
    return "nine|<ts>|<token>|<sig>", "VL-..."

client = GeetestNineClient(
    captcha_id="12cd3cfd6af764f226a0ba1ac9938f06",
    verify_type_provider=my_provider,
)
seccode = client.solve(identifier="user@example.com")
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `GEETEST_ONNX_DIR` | bundled | Override ONNX model dir |

Tuning at construction time:

```python
GeetestNineClient(
    ...,
    threshold=2.2,            # margin threshold for onnx confidence
    session=my_session,       # bring your own curl_cffi session
    proxy={"https": "socks5://..."},
)
```

## Non-goals

- Not a `svg_seed` solver. Mechanic decoded (`SvgSeedSolver` in the source
  repo of Bitdeer-Auto) but verify format not solved; use `BrowserVT` instead
  to get `nine` challenges served.
- Not a captcha-service SDK. This is a self-contained solver library; site
  registration flow is your app's responsibility.

## Layout

```
src/geetest_nine/
├── __init__.py
├── client.py          # GeetestNineClient (site-agnostic flow)
├── protocol.py        # build_w, _pow, _lotParser, encrypt
├── onnx_matcher.py    # OnnxMatcher (ONNX inference)
├── hybrid_vt.py       # BrowserVT (playwright headless)
├── crypto.py          # gen_td_sign
├── tracks/            # nine-click track generators
└── models/            # ONNX artifacts (vision_backbone, match_head)
```

## Credits

Wire protocol port + ABO obfuscation: `xKiian/GeekedTest sign.py` (MIT).

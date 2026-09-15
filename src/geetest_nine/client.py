"""Generic Geetest v4 nine-grid client.

Site-agnostic solver. Dependency-injects:
- ``session``: curl_cffi requests.Session (impersonated).
- ``verify_type_provider``: callable(identifier) -> (verify_type, verify_lot) —
  browser-token grabber; use ``geetest_nine.hybrid_vt.BrowserVT`` or your own.

The Bitdeer flow lives outside this file (see examples/bitdeer_flow.py).
"""

from __future__ import annotations

import json
import time
from typing import Callable
from uuid import uuid4

import curl_cffi.requests as requests

from .onnx_matcher import ONNX_MARGIN_THRESHOLD, OnnxLowConfidence, OnnxMatcher
from .protocol import build_w

_STATIC_BASE = "https://static.geetest.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

VerifyTypeProvider = Callable[[str], tuple[str, str]]  # (identifier) -> (vt, verify_lot)


class GeetestNineClient:
    """Solve Geetest v4 nine-grid captcha end-to-end.

    Example::

        from geetest_nine import GeetestNineClient, BrowserVT

        vt = BrowserVT(signup_url="https://acme.com/signup", ...)
        client = GeetestNineClient(
            captcha_id="12cd3cfd6af764f226a0ba1ac9938f06",
            verify_type_provider=vt.get_vt_for,
        )
        result = client.solve(identifier="user@example.com")
        # result is a dict: {lot_number, captcha_output, pass_token, gen_time}
        # feed into your site's downstream request as headers.
    """

    LOAD_URL = "https://gcaptcha4.geetest.com/load"
    VERIFY_URL = "https://gcaptcha4.geetest.com/verify"

    def __init__(
        self,
        captcha_id: str,
        verify_type_provider: VerifyTypeProvider,
        session: requests.Session | None = None,
        proxy: dict | None = None,
        threshold: float = ONNX_MARGIN_THRESHOLD,
    ):
        self.captcha_id = captcha_id
        self.provider = verify_type_provider
        self.s = session or requests.Session(impersonate="chrome124")
        if proxy:
            self.s.proxies.update(proxy)
        self.s.headers.setdefault("User-Agent", _UA)
        self.threshold = threshold
        self.last_seccode: dict | None = None
        self.verify_lot: str = ""
        self._last_source: str | None = None
        self._last_margin: float | None = None
        self._last_load_data: dict | None = None

    # ── public API ─────────────────────────────────────────────────────────
    def solve(self, identifier: str, retries: int = 4) -> dict:
        """Solve one captcha. Each retry uses a fresh challenge.

        After ``retries`` low-confidence rounds, submit the best-guess indices
        from the last challenge (recovery rate 7/7 in production).
        """
        vt, vlot = self.provider(identifier)
        if not vt:
            raise RuntimeError("verify_type_provider returned empty token")
        self.verify_lot = vlot

        last_err: Exception | None = None
        last_lowconf_indices = None
        for attempt in range(retries):
            try:
                return self._solve_once(vt)
            except OnnxLowConfidence as e:
                last_err = e
                last_lowconf_indices = e.indices
                time.sleep(1 + attempt)
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1 + attempt)
        if last_lowconf_indices is not None:
            self._last_source = "onnx-best-guess"
            self._last_margin = None
            print(
                f"  [best-guess] all {retries} challenges low-confidence "
                f"→ submitting {last_lowconf_indices}"
            )
            return self._submit(
                vt,
                last_lowconf_indices,
                getattr(self, "_last_img_bytes", None),
                getattr(self, "_last_prompt_bytes", None),
                answer_source="onnx-best-guess",
            )
        raise RuntimeError(f"captcha solve failed after {retries} attempts: {last_err}")

    # ── internal ───────────────────────────────────────────────────────────
    def _solve_once(self, verify_type: str) -> dict:
        cb = f"geetest_{int(time.time() * 1000)}"
        r = self.s.get(
            self.LOAD_URL,
            params={
                "captcha_id": self.captcha_id,
                "challenge": str(uuid4()),
                "client_type": "web",
                "risk_type": verify_type,
                "lang": "eng",
                "callback": cb,
            },
            timeout=60,
        )
        data = self._unwrap(r.text, cb)["data"]
        self._last_load_data = data

        ctype = data.get("captcha_type", "")
        if ctype != "nine":
            raise RuntimeError(
                f"unsupported captcha_type={ctype!r} (this library handles nine only)"
            )

        img_bytes = self.s.get(f"{_STATIC_BASE}/{data['imgs']}", timeout=20).content
        prompt_bytes = self.s.get(f"{_STATIC_BASE}/{data['ques'][0]}", timeout=20).content
        self._last_img_bytes = img_bytes
        self._last_prompt_bytes = prompt_bytes

        result = OnnxMatcher.solve(img_bytes, prompt_bytes)
        if result is None:
            raise OnnxLowConfidence("onnx-error", None)
        indices, margin = result
        self._last_margin = margin
        if margin < self.threshold:
            raise OnnxLowConfidence(f"low-margin({margin:.2f})", indices)

        self._last_source = "onnx"
        return self._submit(verify_type, indices, img_bytes, prompt_bytes, answer_source="onnx")

    def _submit(
        self,
        verify_type: str,
        indices: list,
        img_bytes: bytes | None,
        prompt_bytes: bytes | None,
        answer_source: str,
    ) -> dict:
        data = self._last_load_data
        if data is None:
            raise RuntimeError("_submit called without a loaded challenge")
        cells = [[(idx // 3) + 1, (idx % 3) + 1] for idx in sorted(indices)]

        cb2 = f"geetest_{int(time.time() * 1000)}"
        params = {
            "callback": cb2,
            "captcha_id": self.captcha_id,
            "client_type": "web",
            "lot_number": data["lot_number"],
            "risk_type": verify_type,
            "payload": data["payload"],
            "process_token": data["process_token"],
            "payload_protocol": "1",
            "pt": "1",
            "w": build_w(data, self.captcha_id, cells, cells=[(r, c) for r, c in cells]),
        }
        r = self.s.get(self.VERIFY_URL, params=params, timeout=60)
        res = self._unwrap(r.text, cb2)
        d = res.get("data", {})
        print(f"  [wire] result={d.get('result')} answer={cells} src={answer_source}")
        if d.get("result") == "success":
            if "seccode" not in d:
                raise RuntimeError(f"success but no seccode: {json.dumps(d)[:300]}")
            self.last_seccode = d["seccode"]
            return d["seccode"]
        raise RuntimeError(f"verify fail: {json.dumps(res)[:300]} (answer={cells})")

    @staticmethod
    def _unwrap(text: str, callback: str) -> dict:
        return json.loads(text.split(f"{callback}(", 1)[1].rsplit(")", 1)[0])

    # ── introspection ──────────────────────────────────────────────────────
    @property
    def last_source(self) -> str | None:
        return self._last_source

    @property
    def last_margin(self) -> float | None:
        return self._last_margin

"""Browser-based verifyType provider.

Some sites' backends flag non-browser HTTP clients (curl_cffi included) and
respond with an unsolvable ``svg_seed`` challenge instead of ``nine``. Solution:
open the site's signup/login page in headless Chrome, intercept the endpoint
that returns ``verifyType``, and reuse that token for the solver's ``/load`` call.

This module is site-agnostic; configure via ``BrowserVT(...)``.

Example (Bitdeer):

    vt = BrowserVT(
        signup_url="https://account.bitdeer.com/en/sign_up?method=1",
        email_selector='input[placeholder*="Email"]',
        submit_text="Send verification code",
        intercept_url_substring="geeTestForm",
        vt_json_path=("data", "verifyType"),
        lot_json_path=("data", "verifyLot"),
    )
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Sequence


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class BrowserVT:
    """Config for one site's ``verifyType`` capture flow.

    Attributes:
        signup_url: Page whose backend calls the ``verifyType`` endpoint.
        email_selector: CSS selector for the email input (used to fill dummy).
        submit_text: Visible button text to click to trigger the geetest form.
        intercept_url_substring: Match this in ``response.url`` to grab.
        vt_json_path: Tuple path into JSON, e.g. ``("data", "verifyType")``.
        lot_json_path: Tuple path into JSON, e.g. ``("data", "verifyLot")``.
        chrome_path: Path to Chrome binary (default: ``/usr/bin/google-chrome``).
        headless: Use headless mode (default True).
        wait_timeout_ms: Max ms to wait for the intercept after clicking submit.
    """

    signup_url: str
    email_selector: str
    submit_text: str
    intercept_url_substring: str
    vt_json_path: Sequence[str] = ("data", "verifyType")
    lot_json_path: Sequence[str] = ("data", "verifyLot")
    chrome_path: str = "/usr/bin/google-chrome"
    headless: bool = True
    wait_timeout_ms: int = 12000

    async def _grab_async(self, identifier: str) -> tuple[str, str]:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            if self.headless:
                args.append("--headless=new")
            browser = await p.chromium.launch(
                headless=self.headless, executable_path=self.chrome_path, args=args
            )
            ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 800})
            await ctx.add_init_script(
                'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
            )
            page = await ctx.new_page()
            holder: dict = {}

            async def _capture(resp):
                try:
                    j = await resp.json()
                    vt = _dig(j, self.vt_json_path)
                    if vt:
                        holder["vt"] = vt
                        holder["lot"] = _dig(j, self.lot_json_path) or ""
                except Exception:
                    pass

            def on_response(resp):
                if self.intercept_url_substring in resp.url:
                    asyncio.ensure_future(_capture(resp))

            page.on("response", on_response)
            await page.goto(self.signup_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
            await page.locator(self.email_selector).first.fill(identifier)
            await page.wait_for_timeout(300)
            await page.get_by_text(self.submit_text).first.click()

            deadline_ms = int(self.wait_timeout_ms)
            step = 500
            while deadline_ms > 0 and "vt" not in holder:
                await page.wait_for_timeout(step)
                deadline_ms -= step
            await page.wait_for_timeout(300)
            vt = holder.get("vt", "")
            lot = holder.get("lot", "")
            await browser.close()
            return vt, lot

    def get_vt_for(self, identifier: str) -> tuple[str, str]:
        """Synchronous wrapper. Returns ``(verify_type, verify_lot)`` or ``("", "")``."""
        try:
            return asyncio.run(self._grab_async(identifier))
        except Exception as e:  # noqa: BLE001
            print(f"  [hybrid_vt] grab failed: {e}")
            return "", ""


def _dig(d, path: Sequence[str]):
    cur = d
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
        if cur is None:
            return None
    return cur

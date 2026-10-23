# -*- coding: utf-8 -*-
import asyncio
from playwright.async_api import async_playwright

URLS = [
    "https://www.baidu.com",
    "https://mooc1.chaoxing.com/mycourse/studentcourse",
    "https://mooc1-1.chaoxing.com/visit/courses",
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ctx = await browser.new_context(user_agent=ua)
        page = await ctx.new_page()
        page.on("requestfailed", lambda r: print("REQFAIL:", r.url[:80], r.failure))
        page.on("response", lambda r: print("RESP:", r.status, r.url[:80]) if r.status >= 400 else None)
        for url in URLS:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)
                print("OK:", url, "->", resp.status if resp else None, "FINAL_URL:", page.url[:120])
            except Exception as e:
                print("ERR:", url, "->", type(e).__name__, str(e)[:300])
        await browser.close()

asyncio.run(main())

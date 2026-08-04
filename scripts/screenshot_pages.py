#!/usr/bin/env python3
"""截图 jobintel-dashboard 页面 → docs/screenshots/（明细页含公司名，不截）"""
import asyncio, os
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8001"
OUT = os.path.expanduser("~/projects/jobintel-dashboard/docs/screenshots")
os.makedirs(OUT, exist_ok=True)
PAGES = [("index", "/"), ("trend", "/trend")]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 860})
        for name, path in PAGES:
            await page.goto(BASE + path, wait_until="networkidle")
            await page.wait_for_timeout(900)
            await page.screenshot(path=os.path.join(OUT, f"{name}.png"))
            print(f"OK {name}.png")
        await browser.close()

asyncio.run(main())

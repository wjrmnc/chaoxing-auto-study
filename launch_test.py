# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
try:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    print("CHROMIUM_OK")
    b.close()
except Exception as e:
    print("FAIL:", repr(e)[:400])
p.stop()
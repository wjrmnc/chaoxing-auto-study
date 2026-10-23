# -*- coding: utf-8 -*-
import os
from playwright.sync_api import sync_playwright

os.environ["DISPLAY"] = ":0"
p = sync_playwright().start()
try:
    b = p.chromium.launch(
        headless=False,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--lang=zh-CN", "--disable-infobars"],
    )
    print("HEADED_OK")
    b.close()
except Exception as e:
    print("FAIL:", repr(e)[:400])
p.stop()
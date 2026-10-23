# -*- coding: utf-8 -*-
"""在虚拟机上验证 Playwright/Chromium/OCR 是否可用。"""
import sys


def main():
    print("== 1) Playwright 启动 Chromium ==")
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    b = p.chromium.launch(headless=True, args=["--no-sandbox"])
    pg = b.new_page()
    pg.set_content("<h1>中文测试</h1>")
    print("页面内容:", pg.eval_on_selector("h1", "el => el.innerText"))
    b.close()
    p.stop()
    print("Chromium OK")

    print("== 2) OCR (RapidOCR) ==")
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (300, 80), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 20), "hello 麒麟 123", fill="black")
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    from ocr import OCRer

    ocr = OCRer(enabled=True)
    text = ocr.ocr(buf.getvalue())
    print("OCR 识别:", text)
    print("OCR OK")

    print("\n全部环境测试通过")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""用转储的弹窗 HTML 实测 EXTRACT/ANSWER 脚本能否识别视频插入题。"""
import sys

from playwright.sync_api import sync_playwright

from chaoxing import EXTRACT_QUESTIONS_JS, ANSWER_CLICK_JS, VIDEOQUIZ_SUBMIT_JS, PROBE_QUESTION_JS

path = sys.argv[1] if len(sys.argv) > 1 else "popup_course1.html"
letters = sys.argv[2].split(",") if len(sys.argv) > 2 else ["对"]

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
pg = b.new_page()

# 注入转储 HTML（取其 body）
with open(path, "r", encoding="utf-8", errors="replace") as f:
    html = f.read()
pg.set_content(html, timeout=20000, wait_until="domcontentloaded")
pg.wait_for_timeout(800)

print("PROBE_QUESTION:", pg.evaluate(PROBE_QUESTION_JS))
qs = pg.evaluate(EXTRACT_QUESTIONS_JS)
print("EXTRACT 题目数:", len(qs))
for q in qs:
    print("  ", q["type"], "|", q["stem"][:40], "| opts:", [(o["letter"], o["text"]) for o in q["options"]])

if qs:
    ans = pg.evaluate(ANSWER_CLICK_JS, {"idx": qs[0]["idx"], "letters": letters})
    print("点击选项结果:", ans)
    print("SUBMIT:", pg.evaluate(VIDEOQUIZ_SUBMIT_JS))

b.close()
p.stop()
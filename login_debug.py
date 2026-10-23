# -*- coding: utf-8 -*-
"""调试学习通登录页：检查 URL、表单元素、按钮，并截图。"""
from playwright.sync_api import sync_playwright

URL = "https://passport.chaoxing.com/login?refer=https%3A%2F%2Fmooc1.chaoxing.com%2F"

p = sync_playwright().start()
b = p.chromium.launch(headless=False, args=["--no-sandbox"])
pg = b.new_page(viewport={"width": 1366, "height": 900}, locale="zh-CN")
pg.on("dialog", lambda d: d.accept())

pg.goto(URL, timeout=60000)
pg.wait_for_timeout(6000)
print("FINAL URL:", pg.url)
try:
    print("TITLE:", pg.title())
except Exception as e:
    print("TITLE ERR:", e)

inputs = ["input[name='uname']", "input[type='password']", "input[name='password']",
          "input[type='text']", "#username", "#password"]
for s in inputs:
    try:
        print("INPUT", s, "->", pg.locator(s).count())
    except Exception as e:
        print("INPUT", s, "ERR", e)

btns = ["#submit", "button.loginBtn", ".login-btn", "input[value='登录']",
        "button:has-text('登录')", "a:has-text('登录')", ".btn.login"]
for s in btns:
    try:
        print("BTN", s, "->", pg.locator(s).count())
    except Exception as e:
        print("BTN", s, "ERR", e)

try:
    pg.screenshot(path="/root/login_debug.png")
    print("screenshot: /root/login_debug.png")
except Exception as e:
    print("shot err:", e)

b.close()
p.stop()

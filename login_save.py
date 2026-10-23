# -*- coding: utf-8 -*-
"""自动登录学习通并把会话保存到 login_state.json，供学习脚本复用（绕开验证码）。"""
import json

from playwright.sync_api import sync_playwright

c = json.load(open("config.json"))
uname = c.get("username", "")
pwd = c.get("password", "")

p = sync_playwright().start()
b = p.chromium.launch(headless=False, args=["--no-sandbox"])
pg = b.new_page(viewport={"width": 1366, "height": 900}, locale="zh-CN")
pg.on("dialog", lambda d: d.accept())
pg.set_default_timeout(20000)

pg.goto("https://mooc1.chaoxing.com/mycourse/studentcourse", timeout=60000)
pg.wait_for_timeout(3000)
print("URL0:", pg.url)

if "passport" in pg.url and uname and pwd:
    try:
        pg.locator("#phone").fill(uname, timeout=15000)
        pg.locator("#pwd").fill(pwd, timeout=15000)
        print("已填入账号密码，点击登录…")
        pg.locator("#loginBtn, button:has-text('登录')").first.click()
    except Exception as e:
        print("填写/点击失败:", e)
    for i in range(30):
        pg.wait_for_timeout(2000)
        print("%d. URL=%s" % ((i + 1) * 2, pg.url[:120]))
        if "mooc" in pg.url and "passport" not in pg.url:
            break

print("最终 URL:", pg.url)
try:
    pg.context.storage_state(path="login_state.json")
    print("已保存登录会话 -> login_state.json")
except Exception as e:
    print("保存失败:", e)

b.close()
p.stop()
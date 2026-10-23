# -*- coding: utf-8 -*-
import requests

urls = [
    "https://passport.chaoxing.com/login",
    "https://passport.chaoxing.com/login.html",
    "https://passport.chaoxing.com/",
    "https://passport.chaoxing.com/login?refer=https%3A%2F%2Fmooc1.chaoxing.com%2F",
    "https://mooc1.chaoxing.com/mycourse/studentcourse",
    "https://sso.chaoxing.com/login",
    "https://i.chaoxing.com/login",
]

for u in urls:
    try:
        r = requests.get(u, timeout=15, allow_redirects=True)
        title = ""
        if "<title>" in r.text:
            t = r.text.split("<title>")[1].split("</title>")[0].strip()
            title = t[:40]
        print("%-90s %s  title=%s  len=%d" % (u, r.status_code, title, len(r.text)))
    except Exception as e:
        print("%-90s ERR %s" % (u, e))

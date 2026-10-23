# -*- coding: utf-8 -*-
import json

c = json.load(open("config.json"))
print("username :", "YES" if c.get("username") else "NO")
print("password :", "YES" if c.get("password") else "NO")
print("api_key  :", "YES" if (c.get("ai") or {}).get("api_key") else "NO")
print("course   :", c.get("course_name") or "(空=第一个)")
print("mode     :", c.get("finish_mode"), "rate:", c.get("video_rate"))

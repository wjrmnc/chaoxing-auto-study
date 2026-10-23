# -*- coding: utf-8 -*-
"""给 VM 上 config.json 的 ai 段写入 proxy。用法: python set_proxy.py <config路径> <proxy地址>"""
import json
import sys

path = sys.argv[1]
proxy = sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    cfg = json.load(f)
cfg.setdefault("ai", {})["proxy"] = proxy
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("proxy set:", proxy)

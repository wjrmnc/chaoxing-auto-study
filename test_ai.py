# -*- coding: utf-8 -*-
"""验证 VM 上 DeepSeek 经 Clash 代理可用。用法: python3 test_ai.py [题目]"""
import json
import sys
import ai

cfg = json.load(open("config.json", encoding="utf-8"))
solver = ai.AISolver(cfg.get("ai") or {})
q = sys.argv[1] if len(sys.argv) > 1 else "社会发展规律同人的自然选择具有同一性"
try:
    print("judge:", solver.solve_judge(q))
except Exception as e:
    print("FAIL:", str(e)[:300])

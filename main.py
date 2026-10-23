# -*- coding: utf-8 -*-
"""学习通课程自动学习脚本入口。

用法：
    python main.py                     # 使用 config.json
    python main.py --course 课程名      # 指定课程
    python main.py --headless           # 无头模式（不建议，无法扫码）
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="学习通课程自动学习脚本（视频+AI答题+OCR）")
    ap.add_argument("--config", default="config.json", help="配置文件路径（默认 config.json）")
    ap.add_argument("--headless", action="store_true", help="无头模式")
    ap.add_argument("--course", default=None, help="指定课程名称关键字")
    args = ap.parse_args()

    if not os.path.exists(args.config):
        print("未找到配置文件：%s" % args.config)
        print("请先复制 config.example.json 为 config.json 并填写账号、AI key。")
        sys.exit(1)

    cfg = load_config(args.config)
    if args.headless:
        cfg["headless"] = True
    if args.course:
        cfg["course_name"] = args.course

    if not (cfg.get("ai") or {}).get("api_key"):
        print("警告：未配置 AI api_key，题目将无法自动作答（视频照常刷）。")

    from chaoxing import ChaoxingBot

    bot = ChaoxingBot(cfg)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n用户中断，进度已保存。")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
    finally:
        bot.shutdown()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""对截图做 OCR，打印识别文字，帮我们看清 bot 当前界面。"""
import sys

from ocr import OCRer

if len(sys.argv) < 2:
    print("用法: python run_ocr.py <图片路径>")
    sys.exit(1)

ocr = OCRer(enabled=True)
with open(sys.argv[1], "rb") as f:
    data = f.read()
text = ocr.ocr(data)
print("==== OC结果（按行）====")
if text:
    for line in text.splitlines():
        if line.strip():
            print(line)
else:
    print("(未识别到文字)")
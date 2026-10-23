# -*- coding: utf-8 -*-
"""OCR 识别模块：用于识别图片题/图片选项（基于 RapidOCR + onnxruntime，轻量、中文好）。"""
from __future__ import annotations


class OCRer:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self._engine = None

    def _get(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def ocr(self, data: bytes) -> str:
        """传入图片字节流，返回识别出的文本（多行以换行分隔）。失败返回空串。"""
        if not self.enabled or not data:
            return ""
        import io

        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB")
        arr = np.array(img)
        engine = self._get()
        result, _ = engine(arr)
        if not result:
            return ""
        lines = [str(item[1]).strip() for item in result if len(item) > 1 and item[1]]
        return "\n".join(lines).strip()

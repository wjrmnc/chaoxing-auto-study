# -*- coding: utf-8 -*-
"""AI 答题模块：使用 OpenAI 兼容接口（DeepSeek / 智谱 / 通义等）。"""
from __future__ import annotations

import random
import re
import time

import requests


class AISolver:
    def __init__(self, cfg: dict):
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.model = cfg.get("model") or "deepseek-chat"
        self.base_url = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.hint = cfg.get("knowledge_hint") or ""
        self.proxy = cfg.get("proxy") or ""
        self.max_retries = int(cfg.get("max_retries", 3))
        self._down_until = 0.0  # 熔断：AI 失败后一段时间内直接走兜底，避免每题卡几分钟

    # ---------- 基础请求 ----------
    def _endpoint(self):
        base = self.base_url
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _chat(self, messages, temperature=0.1, max_tokens=800, timeout=30):
        if not self.api_key:
            raise RuntimeError("未配置 AI API key（config.json 的 ai.api_key），无法自动答题")
        import time as _t

        if _t.time() < self._down_until:
            raise RuntimeError("AI 服务暂时不可用（熔断中），跳过本次请求")
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        last_err = None
        waits = [2, 5, 10, 20]
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        for i in range(self.max_retries):
            try:
                resp = requests.post(self._endpoint(), headers=headers, json=payload, timeout=timeout, proxies=proxies)
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(waits[i] if i < len(waits) else 30)
                    continue
                if resp.status_code == 401:
                    raise RuntimeError("AI API key 无效(401)")
                resp.raise_for_status()
                data = resp.json()
                return str(data["choices"][0]["message"]["content"]).strip()
            except Exception as e:  # noqa: BLE001
                last_err = e
                self._down_until = time.time() + 300
                time.sleep(waits[i] if i < len(waits) else 30)
        raise RuntimeError("AI 请求失败: %s" % last_err)

    def fallback(self, qtype, options=None, blank_count=0, stem=""):
        """AI 离线时的兜底答案，保证题目能提交、任务点能解锁（答案可能不准确）。"""
        letters = [o.get("letter", "").upper() or chr(65 + i) for i, o in enumerate(options or [])]
        if qtype == "judge":
            # 兜底：对/错 随机，答错会触发"回看"后重答，避免一直踩同一答案
            return "对" if random.random() < 0.5 else "错"
        if qtype == "multi":
            return letters[:2] if letters else ["A", "B"]
        if qtype == "fill":
            return [""] * max(blank_count, 1)
        if qtype == "text":
            return "（AI 暂时不可用，请人工补充作答）"
        return letters[0] if letters else "A"

    def _sys(self):
        hint = (" 课程背景：" + self.hint) if self.hint else ""
        return ("你是课程助教。%s\n只根据题目给出正确答案，回答必须简洁，不要解释过程。" % hint)

    # ---------- 各题型 ----------
    def solve_choice(self, question, options, multi):
        opts_text = "\n".join(
            "%s. %s" % (o.get("letter", chr(65 + i)), o.get("text", "") or "(无文本)")
            for i, o in enumerate(options)
        )
        kind = "多选题" if multi else "单选题"
        prompt = (
            "题目（%s）：\n%s\n\n选项：\n%s\n\n"
            "请只输出答案对应的字母。单选只输出一个字母；多选连续输出多个字母（如 ABD）。"
            "不要输出任何解释、空格或标点。"
            % (kind, question, opts_text)
        )
        content = self._chat([{"role": "system", "content": self._sys()}, {"role": "user", "content": prompt}])
        letters = re.findall(r"[A-Ha-h]", content)
        letters = [x.upper() for x in letters]
        valid = [o.get("letter", "").upper() for o in options]
        letters = [x for x in letters if x in valid]
        if multi:
            return list(dict.fromkeys(letters))
        if letters:
            return letters[0]
        return valid[0] if valid else "A"

    def solve_judge(self, question):
        prompt = "判断题：\n%s\n\n请只回答两个字：正确 或 错误。" % question
        content = self._chat([{"role": "system", "content": self._sys()}, {"role": "user", "content": prompt}])
        if re.search(r"错误|错", content) and not re.search(r"正确|对", content):
            return "错误"
        return "正确"

    def solve_fill(self, question, count):
        prompt = (
            "填空题：\n%s\n\n请给出 %d 个空的答案。每行输出一个空的答案，只输出答案本身，不要任何解释。"
            % (question, max(count, 1))
        )
        content = self._chat([{"role": "system", "content": self._sys()}, {"role": "user", "content": prompt}], temperature=0.1)
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        lines = [re.sub(r"^\d+[.、．)]\s*", "", ln) for ln in lines]
        while len(lines) < count:
            lines.append("")
        return lines[:count]

    def solve_text(self, question):
        prompt = (
            "简答题/论述题：\n%s\n\n请给出完整、准确的参考答案（用于提交作业），"
            "不要任何开场白和解释，直接输出答案正文。" % question
        )
        return self._chat(
            [{"role": "system", "content": self._sys()}, {"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1200,
        )

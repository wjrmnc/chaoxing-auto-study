# -*- coding: utf-8 -*-
"""临时自测：用模拟的学习通题目页面验证 JS 选择器与答题逻辑。"""
import json
import time

from playwright.sync_api import sync_playwright

from chaoxing import (
    EXTRACT_QUESTIONS_JS,
    ANSWER_CLICK_JS,
    ANSWER_FILL_JS,
    CLICK_CONFIRM_JS,
    SAVE_WORK_JS,
    SUBMIT_WORK_JS,
    VIDEO_PROBE_JS,
    VIDEO_FORCE_PLAY_JS,
    VIDEO_JUMP_JS,
    WORK_DETECT_JS,
    NODE_FINISH_JS,
    COURSE_LIST_JS,
    CATALOG_JS,
)

HTML = """<!DOCTYPE html><html><body>
<div class="questionLi">
  <div class="TiMu"><h3>【单选题】在Linux中查看当前目录的命令是？</h3></div>
  <ul class="ans-cc">
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">A</div><div class="answer_p">ls</div></li>
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">B</div><div class="answer_p">cd</div></li>
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">C</div><div class="answer_p">cat</div></li>
  </ul>
</div>
<div class="questionLi">
  <div class="TiMu"><h3>【多选题】下列哪些是Linux文件系统？</h3></div>
  <ul class="ans-cc">
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">A</div><div class="answer_p">ext4</div></li>
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">B</div><div class="answer_p">NTFS</div></li>
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">C</div><div class="answer_p">xfs</div></li>
  </ul>
</div>
<div class="questionLi">
  <div class="TiMu"><h3>【判断题】rm 可以删除目录。</h3></div>
  <ul class="ans-cc">
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">A</div><div class="answer_p">正确</div></li>
    <li class="answerBg" onclick="this.classList.toggle('on')"><div class="mark_name">B</div><div class="answer_p">错误</div></li>
  </ul>
</div>
<div class="questionLi">
  <div class="TiMu"><h3>【填空题】Linux中查看内核版本的命令是 <input class="blank"></h3></div>
</div>
<button class="saveOpt">保存答案</button>
<button class="submitBtn">提交作业</button>
</body></html>"""

HTML_VIDEO = """<!DOCTYPE html><html><body>
<video src="https://example.com/a.mp4" muted></video>
</body></html>"""

HTML_CATALOG = """<!DOCTYPE html><html><body>
<ul class="catalog_ul">
  <li class="catalog_point">
    <a class="click" href="/knowledge/cards?courseid=1&clazzid=2&knowledgeid=11">第一章 视频</a>
    <i class="finish_right"></i>
  </li>
  <li class="catalog_point">
    <a class="click" href="/knowledge/cards?courseid=1&clazzid=2&knowledgeid=12">第一章 测验</a>
  </li>
</ul>
</body></html>"""


def main():
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # ---- 题目提取 ----
    page.set_content(HTML)
    qs = page.evaluate(EXTRACT_QUESTIONS_JS)
    print("提取题目数:", len(qs))
    for q in qs:
        print("  ", q["type"], q["stem"][:30], "opts=", [o["letter"] + ":" + o["text"] for o in q["options"]], "blanks=", q["blankCount"])
    assert len(qs) == 4, "应提取 4 题"
    assert qs[0]["type"] == "single" and qs[1]["type"] == "multi"
    assert qs[2]["type"] == "judge" and qs[3]["type"] == "fill"
    assert qs[3]["blankCount"] == 1

    # ---- 点击选项（按字母）----
    n = page.evaluate(ANSWER_CLICK_JS, {"idx": 0, "letters": ["A"]})
    assert n == 1, "单选点击失败"
    n = page.evaluate(ANSWER_CLICK_JS, {"idx": 1, "letters": ["A", "C"]})
    assert n == 2, "多选点击失败"
    n = page.evaluate(ANSWER_CLICK_JS, {"idx": 2, "letters": ["正确"]})
    assert n == 1, "判断点击失败"
    # 已作答检测：选择后应被判定已作答
    qs2 = page.evaluate(EXTRACT_QUESTIONS_JS)
    remaining = [q for q in qs2 if q["idx"] in (0, 1, 2)]
    print("作答后剩余未答:", len(remaining))
    assert len(remaining) == 0, "已作答题目未被跳过"

    # ---- 填空 ----
    n = page.evaluate(ANSWER_FILL_JS, {"idx": 3, "values": ["uname -a"]})
    assert n == 1, "填空失败"
    val = page.evaluate("() => document.querySelector('.blank').value")
    print("填空结果:", val)
    assert val == "uname -a"

    # ---- 保存/提交 ----
    saved = page.evaluate(SAVE_WORK_JS)
    print("保存按钮点击:", saved)
    sub = page.evaluate(SUBMIT_WORK_JS)
    print("提交按钮点击:", sub)
    assert sub, "提交失败"

    # ---- 确认弹窗按钮 ----
    page.set_content('<div class="interrupt-topic"><button>确定</button></div>')
    ok = page.evaluate(CLICK_CONFIRM_JS)
    assert ok, "确定按钮未点击"
    print("弹窗确定按钮 OK")

    # ---- 作业页检测 ----
    page.set_content('<div class="questionLi"></div><button>提交作业</button>')
    assert page.evaluate(WORK_DETECT_JS) is True
    print("作业页检测 OK")

    # ---- 视频探测/播放/快速完成 ----
    page.set_content(HTML_VIDEO)
    page.wait_for_timeout(500)
    info = page.evaluate(VIDEO_PROBE_JS)
    print("视频探测:", info)
    page.evaluate(VIDEO_FORCE_PLAY_JS.replace("__RATE__", "16"))
    page.wait_for_timeout(600)
    page.evaluate(VIDEO_JUMP_JS)
    info2 = page.evaluate(VIDEO_PROBE_JS)
    print("快速完成后视频状态:", info2)

    # ---- 完成检测 ----
    page.set_content('<div>本节学习已完成</div>')
    assert page.evaluate(NODE_FINISH_JS) is True
    print("完成检测 OK")

    # ---- 课程/目录提取 ----
    page.set_content(HTML_CATALOG)
    cat = page.evaluate(CATALOG_JS)
    print("目录提取:", cat)
    assert len(cat) == 2 and cat[0]["done"] is True and cat[1]["done"] is False

    page.set_content('<div class="courseList"><ul><li class="courseInfo"><a href="/coursedetail/coursedetail?courseid=1&clazzid=2"><div class="courseName">网络安全Linux</div></a></li></ul></div>')
    cs = page.evaluate(COURSE_LIST_JS)
    print("课程提取:", cs)
    assert len(cs) >= 1

    browser.close()
    p.stop()
    print("\n=== 全部自测通过 ===")


if __name__ == "__main__":
    main()

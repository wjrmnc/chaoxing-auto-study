# -*- coding: utf-8 -*-
"""学习通(超星)课程自动学习脚本核心逻辑。

支持：
- 自动登录（账号密码 / 手动扫码）
- 自动遍历课程章节，播放视频（倍速/快速完成）
- 自动作答：课程中插入题、章节测验、作业（接入 AI）
- 图片题 OCR 识别
- 进度断点续学
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import traceback

from playwright.sync_api import sync_playwright

from ai import AISolver
from ocr import OCRer


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# =====================================================================
# 注入到浏览器里的 JS 片段
# =====================================================================

# 提取未作答的题目（.questionLi / .singleQuesId）
EXTRACT_QUESTIONS_JS = r"""
() => {
  const QS = '.questionLi, .singleQuesId, .questionItem, #questionItem, .tkTopic';
  const out = [];
  const qs = document.querySelectorAll(QS);
  qs.forEach((el, idx) => {
    // 只处理可见题目：隐藏卡/未激活卡不处理（避免提前作答/浪费 AI 调用）
    try {
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      if (r.width < 3 || r.height < 3 || st.display === 'none' || st.visibility === 'hidden') return;
    } catch (e) {}
    // 已作答：存在选中态/已填值/已有答案记录（作业回顾页：.Py_answer 含"我的答案/得分"，或 对错图标/只读态）
    const py = el.querySelector('.Py_answer');
    const pyTxt = py ? ((py.innerText || '').replace(/\s+/g, ' ').trim()) : '';
    const recorded = !!py && (!!py.querySelector('.dui, .cuo, i[aria-label]') || /得分[：:]/.test(pyTxt) || /我的答案[：:]\s*\S/.test(pyTxt));
    const hasResult = !!el.querySelector('.dui, .cuo, i[aria-label="答案正确"], i[aria-label="答案错误"]');
    const locked = !!el.querySelector('input:disabled, textarea:disabled, [readonly], .readonly');
    const isAnswered = !!el.querySelector('.on, .check_answer, .selected, .checkAnswer, .answered, input:checked')
        || el.getAttribute('data-answered') === 'true'
        || recorded || hasResult || locked;
    if (isAnswered) return;
    let stem = '';
    const stemEl = el.querySelector('.TiMu, .qtContent, .Zy_TItle, .title, .q_stem, .cardTitle, .subject, .qtDescription, h3, .tkItem_title');
    if (stemEl) stem = (stemEl.innerText || '').trim();
    if (!stem) stem = (el.innerText || '').slice(0, 150).trim();
    const ctx = (el.className || '') + ' ' + stem;
    let type = 'single';
    if (/多选/.test(ctx)) type = 'multi';
    else if (/判断/.test(ctx)) type = 'judge';
    else if (/填空/.test(ctx)) type = 'fill';
    else if (/简答|问答|论述|名词解释/.test(ctx)) type = 'text';
    const options = [];
    const OPS = '.ans-cc .answerBg, .ans-cc li, .ans-cc > div, .option, .opt-item, .ui-checkbox, .ui-radio, .tkItem_ul .ans-videoquiz-opt, .Zy_ulTop > li, .Zy_ulBottom > li, li[role="radio"], li[onkeydown*="inputAddChoice"]';
    const optEls = el.querySelectorAll(OPS);
    optEls.forEach((o, i) => {
      const mk = o.querySelector('.mark_name, .mark, .q_letter, .opt-mark, .fl.mark_name');
      let letter = (mk ? (mk.innerText || '').trim() : '');
      if (!letter) {
        const ar = (o.getAttribute('aria-label') || '').match(/^([A-Ha-h])\s/);
        if (ar) letter = ar[1];
      }
      if (!letter) {
        const ip = o.querySelector('input[type="radio"][value], input[type="checkbox"][value]');
        if (ip && /^[A-Ha-h]$/.test(ip.value || '')) letter = ip.value;
      }
      if (!letter) {
        const lb = (o.querySelector('label') || {}).innerText || o.innerText;
        const lbm = (lb || '').trim().match(/\b([A-Ha-h])\b/);
        if (lbm) letter = lbm[1];
      }
      if (!letter) letter = String.fromCharCode(65 + i);
      const tx = o.querySelector('a.after, .fl.after') || o.querySelector('.answer_p, .ans-p, .option-text, .option-content, .qtOption, .content, label, a, span');
      let text = tx ? (tx.innerText || '').trim() : (o.innerText || '').trim();
      if (/^[A-Ha-h][.、．)）]\s*/.test(text) && !/^[A-Ha-h]$/.test(text)) text = text.replace(/^[A-Ha-h][.、．)）]\s*/, '');
      const images = Array.from(o.querySelectorAll('img')).map(img => img.src || img.getAttribute('data-src') || '').filter(Boolean);
      options.push({ letter: letter, text: text, images: images });
    });
    // 选项文本去掉字母前缀后全部为 对/错/正确/错误 → 本质是判断题
    if (type === 'single' && options.length >= 2 && options.every(o => /^(对|错|正确|错误)$/.test((o.text || '').replace(/^[A-Ha-h][.、．)）]?\s*/, '').trim()))) type = 'judge';
    const blankCount = el.querySelectorAll('input[type="text"], input:not([type]), textarea, .blank').length;
    out.push({ idx: idx, type: type, stem: stem, options: options, blankCount: blankCount });
  });
  return out;
}
"""

# 点击选项（单选/多选/判断）——按字母或文本匹配
ANSWER_CLICK_JS = r"""
(arg) => {
  const idx = arg.idx;
  const letters = arg.letters;
  const QS = '.questionLi, .singleQuesId, .questionItem, #questionItem, .tkTopic';
  const qs = document.querySelectorAll(QS);
  const el = qs[idx];
  if (!el) return 0;
  const OPS = '.ans-cc .answerBg, .ans-cc li, .ans-cc > div, .option, .opt-item, .ui-checkbox, .ui-radio, .tkItem_ul .ans-videoquiz-opt, .Zy_ulTop > li, .Zy_ulBottom > li, li[role="radio"], li[onkeydown*="inputAddChoice"]';
  const optEls = el.querySelectorAll(OPS);
  const set = new Set(letters);
  let clicked = 0;
  optEls.forEach((o) => {
    const mk = o.querySelector('.mark_name, .mark, .q_letter, .opt-mark, .fl.mark_name');
    let letter = (mk ? (mk.innerText || '').trim() : '').toUpperCase();
    if (!letter) {
      const ar = (o.getAttribute('aria-label') || '').match(/^([A-Ha-h])\s/);
      if (ar) letter = ar[1].toUpperCase();
    }
    if (!letter) {
      const ip = o.querySelector('input[type="radio"][value], input[type="checkbox"][value]');
      if (ip && /^[A-Ha-h]$/.test(ip.value || '')) letter = ip.value.toUpperCase();
    }
    const txt = (o.innerText || '').trim();
    let hit = false;
    if (letter && set.has(letter)) hit = true;
    if (/正确|错误|对|错/.test(txt)) {
      const wantRight = set.has('正确') || set.has('对');
      const wantWrong = set.has('错误') || set.has('错');
      if ((/正确|对/.test(txt) && wantRight) || (/错误|错/.test(txt) && wantWrong)) hit = true;
    }
    // 选项无字母标记（如 label 内直接写 "A对"）时，按文本首字母匹配目标答案
    if (!hit && /^[A-Ha-h]/.test(txt) && set.has(txt.charAt(0).toUpperCase())) hit = true;
    if (hit) {
      try {
        // 章节测验：直接勾选 radio/checkbox 并触发事件，再真实点击选项本体（a.after/label）完成交互
        const inp = o.querySelector('input[type="radio"], input[type="checkbox"]');
        if (inp) {
          inp.checked = true;
          try { inp.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) {}
          try { inp.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); } catch (e) {}
        }
        const target = o.querySelector('a.after, .fl.after') || o.querySelector('label') || o;
        try { target.click(); } catch (e) {}
        if (target !== o) { try { o.click(); } catch (e) {} }
      } catch (e) {}
      clicked++;
    }
  });
  return clicked;
}
"""

# 填写空/文本（填空题、简答题）
ANSWER_FILL_JS = r"""
(arg) => {
  const idx = arg.idx;
  const values = arg.values;
  const QS = '.questionLi, .singleQuesId, .questionItem, #questionItem, .tkTopic';
  const qs = document.querySelectorAll(QS);
  const el = qs[idx];
  if (!el) return 0;
  const inputs = Array.from(el.querySelectorAll('input[type="text"], input:not([type]), textarea, .blank'));
  let n = 0;
  values.forEach((v, i) => {
    if (inputs[i]) {
      try {
        inputs[i].value = v;
        inputs[i].dispatchEvent(new Event('input', { bubbles: true }));
        inputs[i].dispatchEvent(new Event('change', { bubbles: true }));
        try { inputs[i].blur(); } catch (e) {}
        n++;
      } catch (e) {}
    }
  });
  return n;
}
"""

# 点击弹窗里的 确定/继续/开始答题 等
CLICK_CONFIRM_JS = r"""
() => {
  const all = Array.from(document.querySelectorAll('button, a.btn, .btn, input[type="button"], .submitBtn, .submitBtn2, .saveOpt, .next, .continue, .jumpBtn, .interrupt-topic button, .bluebtn, a.bluebtn, [role="button"]'));
  const vis = all.filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const order = [/确\s*定/, /继\s*续/, /下一题/, /下一步/, /开始答题/, /答题完成/, /继续学习/];
  for (const re of order) {
    const hit = vis.find(b => re.test(b.innerText || b.value || b.textContent || ''));
    if (hit) { try { hit.click(); } catch (e) {} return true; }
  }
  return false;
}
"""

# 逐题保存答案（作业页）
SAVE_WORK_JS = r"""
() => {
  const btns = Array.from(document.querySelectorAll('.saveOpt, .saveBtn, .submitBtn, button, input[type="button"]'));
  const vis = btns.filter(b => /保存/.test(b.innerText || b.value || '') && b.getBoundingClientRect().width > 0);
  let n = 0;
  vis.forEach(b => { try { b.click(); n++; } catch (e) {} });
  return n;
}
"""

# 视频内插入题专用：作答后点“提交”，再点“继续”恢复播放
VIDEOQUIZ_SUBMIT_JS = r"""
() => {
  const topics = Array.from(document.querySelectorAll('.tkTopic')).filter(el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && st.display !== 'none';
  });
  if (!topics.length) return 'none';
  const topic = topics[0];
  const submit = topic.querySelector('#videoquiz-submit');
  const submitting = topic.querySelector('#videoquiz-submitting');
  const has = topic.querySelector('#spanHas');
  const not = topic.querySelector('#spanNot');
  const cont = topic.querySelector('#videoquiz-continue');
  const show = (el) => { try { return el && el.style.display !== 'none'; } catch (e) { return false; } };
  if (show(has) || show(not)) {
    if (show(cont)) { try { cont.click(); } catch (e) {} return 'continue'; }
    return 'answered';
  }
  if (show(submitting)) return 'submitting';
  if (submit && show(submit) && !submit.disabled) {
    if (topic.querySelector('input:checked') || topic.querySelector('.on, .choosed, .selected')) {
      try { submit.click(); } catch (e) {}
      return 'submit';
    }
  }
  return 'waiting';
}
"""

# 是否出现"疑似题目"但 EXTRACT 没匹配到（用于兜底与调试）
PROBE_QUESTION_JS = r"""
() => {
  const re = /判断题|单选题|多选题|填空题|简答题|问答题|[AB][、．.]\s*(对|错)/;
  for (const el of document.querySelectorAll('div,section,li,dl,ul')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length < 4 || t.length > 700) continue;
    if (!re.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 3 && r.height > 3) return true;
  }
  return false;
}
"""

# 通用弹窗兜底：对不含标准 .questionLi 结构的题目，点第一个选项并确认
GENERIC_POPUP_JS = r"""
() => {
  let acted = 0;
  const reQ = /判断题|单选题|多选题|填空题|解答题|问答题|选择题/;
  const els = Array.from(document.querySelectorAll('div,section,li,dl,ul'));
  for (const el of els) {
    const t = (el.innerText || '').trim();
    if (!t || t.length < 5 || t.length > 700 || !reQ.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    if (el.querySelector('.on, .check_answer, .answered, .choosed, .selected, .checked')) continue;
    const opts = Array.from(el.querySelectorAll('li,div,span,p')).filter(o => {
      if (o === el || o.children.length > 2) return false;
      const ot = (o.innerText || '').trim();
      const or = o.getBoundingClientRect();
      if (!ot || ot.length > 8 || ot === t || or.width < 3) return false;
      return /^([A-D][、．.]?)?\s*(对|错|正确|错误)\s*$/.test(ot) || /^[A-D][、．.]\s*[^\n]{1,20}$/.test(ot);
    });
    let picked = opts.length ? opts[0] : null;
    if (!picked) picked = Array.from(el.querySelectorAll('li,div')).find(o => /^[A-D][、．.]/.test((o.innerText || '').trim()));
    if (picked) {
      try { picked.click(); acted++; } catch (e) {}
    }
  }
  return acted;
}
"""
SUBMIT_WORK_JS = r"""
() => {
  const btns = Array.from(document.querySelectorAll('.submitBtn, .submitBtn2, #submitBtn, button, input[type="submit"], input[type="button"], a.Btn_blue_1, a.workBtnIndex, a[onclick*="ubmit"], [role="button"], a.bluebtn'));
  const vis = btns.filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  for (const b of vis) {
    const t = (b.innerText || b.value || '').trim();
    if (t && /提交|交卷|完成/.test(t) && !/保存/.test(t)) {
      if (b.disabled || b.className.indexOf('countDown') >= 0) continue;
      b.setAttribute('data-bot-submit', '1');
      return true;
    }
  }
  return false;
}
"""

# 探测当前页/帧里的视频状态
VIDEO_PROBE_JS = r"""
() => {
  const videos = Array.from(document.querySelectorAll('video'));
  if (!videos.length) return { found: false };
  const visible = videos.filter(x => { const r = x.getBoundingClientRect(); return r.width > 10 && r.height > 10; });
  const cand = visible.length ? visible : videos;
  const v = cand.find(x => !x.paused) || cand[0];
  const dur = isFinite(v.duration) ? v.duration : 0;
  return { found: true, cur: v.currentTime || 0, dur: dur, paused: !!v.paused, ended: !!v.ended };
}
"""

# 强制播放 + 倍速
VIDEO_FORCE_PLAY_JS = r"""
() => {
  const rate = __RATE__;
  let n = 0;
  document.querySelectorAll('video').forEach(v => {
    try { v.muted = true; } catch (e) {}
    if (v.paused) { try { const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (e) {} }
    try { if (Math.abs(v.playbackRate - rate) > 0.01) v.playbackRate = rate; } catch (e) {}
    n++;
  });
  return n;
}
"""

# 视频停在结尾但任务未完成时：归零 + 播放 + 倍速（重新累计真实观看时长）
REPLAY_JS = r"""
() => {
  const rate = __RATE__;
  let n = 0;
  document.querySelectorAll('video').forEach(v => {
    try {
      if (v.duration && isFinite(v.duration) && v.duration > 5) { v.muted = true; v.currentTime = 0; n++; }
    } catch (e) {}
    try { const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (e) {}
    try { v.playbackRate = rate; } catch (e) {}
  });
  return n;
}
"""

# 用 video-js 播放器 API 归零并开始播放（比改原生 <video> 可靠）
PLAYER_RESTART_JS = r"""
() => {
  const rate = __RATE__;
  let p = null;
  const v = document.querySelector('video');
  if (v && v.component) { try { p = v.component.player_; } catch (e) {} }
  if (!p) {
    for (const k of ['czplayer', 'player', 'videojs', '_player']) {
      try { if (window[k] && typeof window[k].currentTime === 'function') { p = window[k]; break; } } catch (e) {}
    }
  }
  if (p) {
    try { p.currentTime(0); } catch (e) {}
    try { p.playbackRate(rate); } catch (e) {}
    try { const pr = p.play(); if (pr && pr.catch) pr.catch(() => {}); } catch (e) {}
    return 'player';
  }
  if (v) {
    try { v.muted = true; v.currentTime = 0; } catch (e) {}
    try { const pr = v.play(); if (pr && pr.catch) pr.catch(() => {}); } catch (e) {}
    try { v.playbackRate = rate; } catch (e) {}
    return 'video';
  }
  return 'none';
}
"""

# 保证播放器在播放中（暂停时恢复），维持 1 倍速
PLAYER_PLAY_JS = r"""
() => {
  const rate = __RATE__;
  let p = null;
  const v = document.querySelector('video');
  if (v && v.component) { try { p = v.component.player_; } catch (e) {} }
  if (!p) {
    for (const k of ['czplayer', 'player', 'videojs', '_player']) {
      try { if (window[k] && typeof window[k].currentTime === 'function') { p = window[k]; break; } } catch (e) {}
    }
  }
  if (p) {
    try { if (p.paused()) { const pr = p.play(); if (pr && pr.catch) pr.catch(() => {}); } } catch (e) {}
    try { p.playbackRate(rate); } catch (e) {}
    return 'player';
  }
  if (v) {
    try { if (v.paused) { v.muted = true; const pr = v.play(); if (pr && pr.catch) pr.catch(() => {}); } } catch (e) {}
    try { v.playbackRate = rate; } catch (e) {}
    return 'video';
  }
  return 'none';
}
"""

# 快速完成模式：把视频拖到结尾并触发上报/结束
VIDEO_JUMP_JS = r"""
() => {
  let done = 0;
  document.querySelectorAll('video').forEach(v => {
    if (v.duration && isFinite(v.duration) && v.duration > 5 && v.currentTime < v.duration - 1) {
      try {
        v.currentTime = v.duration - 0.5;
        v.dispatchEvent(new Event('ended'));
        done++;
      } catch (e) {}
    }
  });
  try {
    if (window.wrapPlayVideo) {
      if (window.wrapPlayVideo.currentTime) window.wrapPlayVideo.currentTime = 999999;
      if (window.wrapPlayVideo.sendLog) window.wrapPlayVideo.sendLog();
      if (window.wrapPlayVideo.ended) window.wrapPlayVideo.ended();
      if (window.wrapPlayVideo.finish) window.wrapPlayVideo.finish();
    }
  } catch (e) {}
  return done;
}
"""

# 本节是否已完成（完成提示 or 出现“下一个”按钮）
# 注意：作业/测验帧(work/doHomeWork/selectWorkQuestion)的“返回课程/下一节”按钮不代表完成，
# 必须排除，否则从上一节点“下一节”进入 3.2 时残留的作业帧会触发误判、跳过本课。
NODE_FINISH_JS = r"""
() => {
  try {
    const url = location.href || '';
    if (/\/work\//i.test(url) || /doHomeWork/i.test(url) || /selectWorkQuestion/i.test(url)) return false;
    const t = document.body ? (document.body.innerText || '') : '';
    if (/本节已完成|本节学习已完成|本节内容已完成|完成本节学习|您已完成本节|学习完成/.test(t)) return true;
    const btns = Array.from(document.querySelectorAll('a, button, .btn'));
    const vis = btns.filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    return !!vis.find(b => /下一个|下一节|返回目录|返回课程/.test(b.innerText || b.textContent || ''));
  } catch (e) { return false; }
}
"""

# 当前帧是否是作业/测验页（有题目元素，且存在答题/提交特征）
WORK_DETECT_JS = r"""
() => {
  const url = location.href || '';
  if (/doHomeWork|work[\/?]/i.test(url)) return true;
  const qs = document.querySelectorAll('.questionLi, .singleQuesId');
  if (!qs.length) return false;
  const txt = document.body ? (document.body.innerText || '') : '';
  return /保存答案|提交作业|交卷|开始答题/.test(txt) || qs.length >= 1;
}
"""

# 课程列表提取
COURSE_LIST_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const els = document.querySelectorAll('a[href*="stucoursemiddle"], a[href*="coursedetail"], a[href*="course/study"], a[href*="visit/course"], a[href*="courseid"]');
  els.forEach(a => {
    const href = a.getAttribute('href') || '';
    if (!href || seen.has(href)) return;
    seen.add(href);
    let name = (a.innerText || a.title || '').trim();
    if (!name) {
      const p = a.closest('.courseInfo, .course, .courseCard, li');
      if (p) {
        const t = (p.innerText || '').trim().split('\n').map(s => s.trim()).filter(Boolean);
        name = t[0] || '';
      }
    }
    if (name) out.push({ name: name, href: href });
  });
  if (!out.length) {
    document.querySelectorAll('.courseCover img, .course img').forEach(img => {
      const name = img.getAttribute('alt') || img.getAttribute('title') || '';
      const a = img.closest('a');
      if (name && a) out.push({ name: name, href: a.getAttribute('href') || '' });
    });
  }
  return out;
}
"""

# 目录知识点链接提取
CATALOG_JS = r"""
() => {
  const HIT = 'a[href*="knowledge/cards"], a[href*="knowledgeid"], a[href*="studentstudy"], a[href*="card"]';
  const out = [];
  const seen = new Set();

  const isDone = (a, li) => {
    // 1) 类名含完成标志
    const cls = (a.className || '') + ' ' + (li ? (li.className || '') : '') + ' ' + ((li && li.parentElement) ? (li.parentElement.className || '') : '');
    if (/(done|succeed|finished|completed|studied|already_ok)/i.test(cls)) return true;
    // 2) 状态图标：em/span 带完成类 或 图片 src 含完成标志
    const scope = li || a.parentElement || a;
    if (scope) {
      for (const em of scope.querySelectorAll('em, i, span')) {
        const c = em.className || '';
        if (c && /(done|succeed|finish|ok)/i.test(c) && !/openlock/.test(c)) return true;
      }
      for (const img of scope.querySelectorAll('img')) {
        const src = ((img.getAttribute('src') || '') + ' ' + (img.getAttribute('style') || '')).toLowerCase();
        if (/(done|ok|finish|succeed|green|right|already)/.test(src)) return true;
      }
    }
    return false;
  };

  const nodes = document.querySelectorAll('.catalog_point, .catalogPoint, li[class*="catalog_point"], li[class*="knowledge"]');
  nodes.forEach(li => {
    const a = li.querySelector(HIT);
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (!href || seen.has(href)) return;
    seen.add(href);
    out.push({
      name: (a.innerText || a.getAttribute('title') || '').trim(),
      href: href,
      done: isDone(a, li)
    });
  });
  if (!out.length) {
    document.querySelectorAll(HIT).forEach(a => {
      const href = a.getAttribute('href') || '';
      if (href && !seen.has(href)) { seen.add(href); out.push({ name: (a.innerText || '').trim(), href: href, done: isDone(a, a.closest('h3') || a.parentElement) }); }
    });
  }
  return out;
}
"""

# 下载图片字节（供 OCR）
OCR_FETCH_JS = r"""
async (src) => {
  const r = await fetch(src, { credentials: 'include', cache: 'no-cache' });
  const b = await r.arrayBuffer();
  return Array.from(new Uint8Array(b));
}
"""


# =====================================================================
# 主机器人
# =====================================================================
class ChaoxingBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ai = AISolver(cfg.get("ai") or {})
        self.ocr = OCRer(bool((cfg.get("ocr") or {}).get("enabled", True)))
        self.progress_file = cfg.get("progress_file", "progress.json")
        self.done = self._load_progress()
        self._submitted = set()
        self._replayed = set()
        self._initdata_quizzes = []
        self._inserted_seeked = set()
        self._debug_dump_keys = {}
        self._answered_sig = {}
        self._lesson_key = ""
        self.video_rate = float(cfg.get("video_rate", 1.0))
        self.finish_mode = str(cfg.get("finish_mode", "watch"))
        self.fast_jump = bool(cfg.get("fast_jump", True))
        self.poll = float(cfg.get("poll_interval", 2.5))
        self.idle_timeout = float(cfg.get("idle_timeout", 25))
        self.wait_timeout = float(cfg.get("wait_timeout", 60))
        self.per_knowledge_timeout = float(cfg.get("per_knowledge_timeout", 3600))
        self.per_lesson_timeout = float(cfg.get("per_lesson_timeout", 3600))
        self.per_course_timeout = float(cfg.get("per_course_timeout", 90000))
        self.ended_grace = float(cfg.get("ended_grace", 120))
        self.kp_grace = float(cfg.get("kp_grace", 30))
        self.kp_next_max = int(cfg.get("kp_next_max", 10))
        self.skip_done = bool(cfg.get("skip_done", True))
        self.max_courses = int(cfg.get("max_courses", 3))
        self.debug_html = bool(cfg.get("debug_html", False))
        self.pw = None
        self.browser = None
        self.page = None

    # ---------------- 进度持久化 ----------------
    def _load_progress(self):
        try:
            with open(self.progress_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_progress(self):
        try:
            with open(self.progress_file, "w", encoding="utf-8") as f:
                json.dump(self.done, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log("保存进度失败:", e)

    # ---------------- 浏览器 ----------------
    def _launch(self):
        headless = bool(self.cfg.get("headless", False))
        browser = str(self.cfg.get("browser", "chromium"))
        self.pw = sync_playwright().start()
        args = ["--no-sandbox", "--disable-blink-features=AutomationControlled", "--lang=zh-CN", "--disable-infobars"]
        tried_headless = False
        try:
            if browser == "firefox":
                self.browser = self.pw.firefox.launch(headless=headless)
            elif browser == "webkit":
                self.browser = self.pw.webkit.launch(headless=headless)
            else:
                self.browser = self.pw.chromium.launch(headless=headless, args=args)
        except Exception as e:
            log("启动 Chromium 失败，尝试无头:", e)
            tried_headless = True
            if browser == "firefox":
                self.browser = self.pw.firefox.launch(headless=True)
            elif browser == "webkit":
                self.browser = self.pw.webkit.launch(headless=True)
            else:
                self.browser = self.pw.chromium.launch(headless=True, args=args)
        if tried_headless:
            log("已切换为无头模式（有头启动失败）")
        # 优先复用已保存的登录会话
        state_file = self.cfg.get("state_file", "login_state.json")
        kw = {"viewport": {"width": 1366, "height": 900}, "locale": "zh-CN", "ignore_https_errors": True,
              "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
        if os.path.exists(state_file):
            try:
                kw["storage_state"] = state_file
                log("复用已保存的登录会话:", state_file)
            except Exception:
                pass
        ctx = self.browser.new_context(**kw)
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        self.page = ctx.new_page()
        self.page.set_default_timeout(int(self.wait_timeout * 1000))
        self.page.on("dialog", self._on_dialog)
        self.page.on("response", self._on_response)

    def _on_dialog(self, d):
        """自动接受弹窗；若为作业提交失败，清除该作业的已提交标记以便重试。"""
        try:
            msg = (d.message or "")[:80]
            log("自动确认弹窗:", msg)
            if "作业提交失败" in msg:
                key = self.page.url if hasattr(self, "page") else ""
                if key in self._submitted:
                    self._submitted.discard(key)
                    log("检测到作业提交失败，已清除提交标记以便重试")
        except Exception:
            pass
        try:
            d.accept()
        except Exception:
            pass

    def _submit_work_click(self, frame):
        """找到作业页“提交”按钮并用 Playwright 真实鼠标点击。
        enc_js_work.js 的 getEnc() 依赖真实点击坐标 (isTrusted=true)，
        JS 合成 click 只会得到 (0|0)/(NaN|NaN)，导致服务端指纹校验失败。"""
        try:
            if not frame.evaluate(SUBMIT_WORK_JS):
                return False
        except Exception:
            return False
        try:
            loc = frame.locator('[data-bot-submit="1"]').first
            try:
                loc.click(timeout=4000)
            except Exception:
                loc.click(timeout=4000, force=True)
            return True
        except Exception:
            return False

    def _on_response(self, resp):
        """捕获视频播放器 initdata，解析视频内插入题信息；并记录作业提交(addStudentWorkNewWeb)的请求/响应，便于排查提交是否成功。"""
        try:
            if "addStudentWorkNewWeb" in resp.url:
                try:
                    body = resp.text()
                    post = (resp.request.post_data or "")[:2000]
                    log("作业提交响应: %s" % (body[:400],))
                    with open("work_submit_trace.log", "a", encoding="utf-8") as f:
                        f.write("\n[%s] url=%s\nPOST: %s\nRESP: %s\n"
                                % (time.strftime("%m-%d %H:%M:%S"), resp.url[:400], post, body[:2000]))
                except Exception as e:
                    log("作业提交响应读取失败:", str(e)[:80])
                return
        except Exception:
            pass
        try:
            if "initdatawithviewerV2" in resp.url:
                txt = resp.text()
                data = json.loads(txt)
                if not isinstance(data, list):
                    data = [data]
                for item in data:
                    for d in item.get("datas", []) or []:
                        if d.get("questionType"):
                            try:
                                st = float(d.get("startTime") or 0)
                            except (TypeError, ValueError):
                                st = 0.0
                            self._initdata_quizzes.append({
                                "startTime": st,
                                "answered": bool(d.get("answered")),
                                "description": str(d.get("description") or "")[:200],
                                "questionType": str(d.get("questionType") or ""),
                                "resourceId": d.get("resourceId"),
                            })
        except Exception:
            pass

    def _save_state(self):
        try:
            path = self.cfg.get("state_file", "login_state.json")
            self.page.context.storage_state(path=path)
            log("已保存登录状态 ->", path)
        except Exception as e:
            log("保存登录状态失败:", e)

    def shutdown(self):
        self._save_progress()
        for name in ("page", "browser"):
            obj = getattr(self, name, None)
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        if self.pw:
            try:
                self.pw.stop()
            except Exception:
                pass

    def _frames(self):
        frames = [self.page]
        try:
            for f in self.page.frames:
                if f not in frames:
                    frames.append(f)
        except Exception:
            pass
        return frames

    def _goto(self, page, url, timeout=None, retries=3):
        """稳健导航：不等待全量资源加载（防超时），失败自动重试。"""
        timeout = timeout or int(self.wait_timeout * 1000)
        for i in range(retries):
            try:
                page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                return True
            except Exception as e:
                log("页面加载失败(%d/%d): %s" % (i + 1, retries, url[-90:]))
                try:
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
        return False

    # ---------------- 登录 ----------------
    def login(self):
        page = self.page
        uname = (self.cfg.get("username") or "").strip()
        pwd = (self.cfg.get("password") or "").strip()
        for attempt in range(4):
            # 直接打开课程列表页；未登录时学习通会自动跳转到 passport2 登录页（旧 passport 地址已失效）
            self._goto(page, "https://mooc1.chaoxing.com/mycourse/studentcourse")
            page.wait_for_timeout(3000)
            try:
                log("当前页面:", page.url[:120])
            except Exception:
                pass
            if self._logged_in():
                log("已处于登录状态（复用保存的会话），直接开始")
                return
            if uname and pwd:
                log("未登录，尝试自动登录(第%d次)" % (attempt + 1))
                try:
                    if self._password_login(uname, pwd):
                        log("登录成功")
                        self._save_state()
                        return
                except Exception as e:
                    log("账号密码登录异常:", e)
            else:
                # 无凭据：进入手动登录等待（仅在有头窗口下使用）
                self._manual_login()
                self._save_state()
                return
            log("登录未成功，稍后重试…")
            try:
                page.wait_for_timeout(5000)
            except Exception:
                time.sleep(5)
        log("多次登录尝试均失败，退出等待系统重启")
        raise RuntimeError("login 连续失败")

    def _password_login(self, uname, pwd):
        page = self.page
        # 等登录表单出现；等待期间若检测到已登录则直接成功
        form = False
        start = time.time()
        while time.time() - start < 20:
            if self._logged_in():
                return True
            try:
                if page.locator("#phone").count() and page.locator("#loginBtn").count():
                    form = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)
        if not form:
            try:
                log("未找到登录表单，当前页面:", page.url[:120])
            except Exception:
                pass
            return self._logged_in()
        # 勾选用户协议等
        try:
            cb = page.locator("input[type='checkbox'], .agree, .checkbox")
            if cb.count() and not (cb.first.is_checked() if cb.first.count() else False):
                cb.first.click(force=True)
        except Exception:
            pass
        # 优先使用精确 ID（避免组合选择器 .first 误选到前面隐藏输入框）
        try:
            if page.locator("#phone").count():
                page.locator("#phone").first.fill(uname)
            else:
                page.locator("input[placeholder*='手机号'], input[placeholder*='学号'], input[name='uname'], input[type='text']").first.fill(uname)
        except Exception:
            pass
        try:
            if page.locator("#pwd").count():
                page.locator("#pwd").first.fill(pwd)
            else:
                page.locator("input[type='password']").first.fill(pwd)
        except Exception:
            pass
        try:
            if page.locator("#loginBtn").count():
                page.locator("#loginBtn").first.click()
            else:
                page.locator("button:has-text('登录'), .btn-big-blue").first.click()
        except Exception:
            pass
        for _ in range(30):
            page.wait_for_timeout(1500)
            if self._logged_in():
                return True
            try:
                if page.locator("iframe[src*='captcha'], iframe[src*='verify'], .yidun, iframe[id*='captcha']").count() > 0:
                    log("检测到验证码，请在浏览器里手动完成验证")
                    self._manual_login()
                    return True
            except Exception:
                pass
            # 失败诊断：~9 秒后仍未成功则打印登录页提示
            if _ == 6:
                try:
                    t = page.evaluate("() => document.body ? document.body.innerText : ''")
                    log("登录未成功，当前登录页提示:", t[:180].replace("\n", " | "))
                except Exception as e:
                    log("无法读取登录页状态:", e)
        return self._logged_in()

    def _logged_in(self):
        try:
            url = self.page.url
        except Exception:
            return False
        # 只看域名（host），避免 URL 查询参数里的 passport2 字样误判
        host = re.sub(r"^[a-z]+://", "", url).split("/")[0].split(":")[0].lower()
        if "passport" in host:
            return False
        return "mooc" in host or "chaoxing.com" in host

    def _manual_login(self):
        last = 0
        log("等待手动登录… 请在浏览器窗口中完成登录（密码/扫码/验证码），检测到登录成功后自动继续")
        while not self._logged_in():
            try:
                self.page.wait_for_timeout(3000)
            except Exception:
                time.sleep(3)
            if time.time() - last > 30:
                last = time.time()
                log("仍在等待手动登录完成…")

    # ---------------- 课程与章节 ----------------
    def open_course_list(self):
        # 该校部署在 mooc1-1，标准 mycourse/studentcourse 会重定向到学校门户，课程列表在此
        self._goto(self.page, "https://mooc1-1.chaoxing.com/visit/courses")
        self.page.wait_for_timeout(3000)

    def get_courses(self):
        for _ in range(10):
            try:
                cs = self.page.evaluate(COURSE_LIST_JS)
            except Exception:
                cs = []
            if cs:
                return cs
            self.page.wait_for_timeout(2000)
        return []

    def ensure_study_page(self):
        page = self.page
        for _ in range(15):
            try:
                url = page.url
            except Exception:
                url = ""
            if "passport" in url:
                page.goto("https://mooc1.chaoxing.com/mycourse/studentcourse")
                page.wait_for_timeout(2000)
                continue
            if "coursedetail" in url:
                try:
                    page.locator("a:has-text('章节'), .tab:has-text('章节'), li:has-text('章节') a").first.click()
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                continue
            self._expand_catalog()
            page.wait_for_timeout(1500)
            links = self._catalog()
            if links:
                return links
            page.wait_for_timeout(1500)
        m = re.search(r"[cC]ourse[Ii]d=(\d+)", page.url)
        cl = re.search(r"clazzid=(\d+)", page.url)
        if m and cl:
            url = "https://mooc1.chaoxing.com/visit/course/study?courseId=%s&clazzid=%s" % (m.group(1), cl.group(1))
            page.goto(url)
            page.wait_for_timeout(3000)
            links = self._catalog()
            if links:
                return links
        return []

    def _catalog(self):
        try:
            return self.page.evaluate(CATALOG_JS)
        except Exception:
            return []

    def _expand_catalog(self):
        """只点击真正可折叠的章节名/按钮（保守，避免误点到导航）。"""
        try:
            self.page.evaluate(
                """() => {
                  let n = 0;
                  document.querySelectorAll('[onclick*="collaps"], [onclick*="showChapter"], [onclick*="openChapter"], [class*="chapterDiv"], [class="catalog_btn"], [class*="unitTitle"][onclick]').forEach(el => {
                    try {
                      if (el.tagName !== 'A' && el.getBoundingClientRect().width > 5) { el.click(); n++; }
                    } catch (e) {}
                  });
                  return n;
                }"""
            )
        except Exception:
            pass

    def _resolve(self, href):
        from urllib.parse import urljoin

        href = (href or "").strip()
        if not href:
            return ""
        try:
            return urljoin(self.page.url, href)
        except Exception:
            if href.startswith("http"):
                return href
            if href.startswith("//"):
                return "https:" + href
            return "https://mooc1-1.chaoxing.com" + (href if href.startswith("/") else "/" + href)

    # ---------------- 主流程 ----------------
    def _course_keywords(self):
        """返回要刷的课程名称关键字列表（course_name 或 course_names）。空列表=全部。"""
        keys = []
        n = (self.cfg.get("course_name") or "").strip()
        if n:
            keys.append(n)
        for k in (self.cfg.get("course_names") or []):
            k = str(k or "").strip()
            if k:
                keys.append(k)
        return keys

    def run(self):
        self._launch()
        try:
            courses = None
            for cycle in range(8):
                try:
                    self.login()
                    self.open_course_list()
                    cs = self.get_courses()
                except Exception as e:
                    log("初始化失败(第%d轮): %s" % (cycle + 1, e))
                    cs = []
                if cs:
                    courses = cs
                    break
                log("第%d轮未拿到课程列表（网络或页面异常），60秒后重试" % (cycle + 1))
                try:
                    self.page.wait_for_timeout(60000)
                except Exception:
                    time.sleep(60)
            if courses is None:
                log("8轮重试均未拿到课程列表，退出等待系统重启")
                raise RuntimeError("课程列表获取失败")
            keys = self._course_keywords()
            if keys:
                before = len(courses)
                courses = [c for c in courses if any(k in c["name"] for k in keys)]
                log("课程过滤: 关键字=%s, 匹配到 %d/%d 门" % (keys, len(courses), before))
            if not courses:
                log("未找到任何课程（名称含%s）" % keys)
                raise RuntimeError("关键字未匹配到课程")
            total = len(courses[: self.max_courses])
            for i, c in enumerate(courses[: self.max_courses], 1):
                log("========== 课程 %d/%d: %s ==========" % (i, total, c["name"]))
                try:
                    self.study_course(c)
                except Exception as e:
                    log("课程处理异常，跳过该课程:", e)
                    try:
                        import traceback as _tb
                        _tb.print_exc()
                    except Exception:
                        pass
            log("全部完成，祝学习顺利！")
        finally:
            self.shutdown()

    def study_course(self, course):
        page = self.page
        href = course["href"]
        if not href.startswith("http"):
            href = "https://mooc1-1.chaoxing.com" + (href if href.startswith("/") else "/" + href)
        log("打开课程页:", href)
        self._goto(page, href)
        page.wait_for_timeout(4000)
        # 只有第1章/已解锁课时的链接是真实地址，用它作为入口
        first = None
        try:
            first = page.evaluate(
                "() => { const a = document.querySelector('a[href*=\"studentstudy\"], a[href*=\"knowledge/cards\"]'); return a ? a.getAttribute('href') : null; }"
            )
        except Exception:
            first = None
        if not first:
            log("未找到课时入口，跳过课程:", course["name"])
            return
        first_url = self._resolve(first)
        self._study_tpl = re.sub(r"chapterId=\d+", "chapterId={}", first_url)
        log("进入第一课时:", first_url[:110])
        self._goto(page, first_url)
        page.wait_for_timeout(3000)
        page.wait_for_timeout(5000)
        # 侧边栏提取全部课时（用于进度显示/去重），导航用顺序“下一节”
        lessons = self._extract_full_lessons()
        total = len(lessons)
        name_by_id = {}
        for l_ in lessons:
            if l_.get("cid"):
                name_by_id[l_["cid"]] = l_.get("name") or l_["cid"]
        log("该课程共 %d 个课时，开始顺序学习（每节课做完自动进入下一节）" % total)
        # 断点续学：优先依据服务器侧边栏的真实完成状态（progress.json 会与服务器失步——
        # 若某课时被误记为已完成，断点会跳到其后的“章节未开放”锁定课时，从而空转结束课程）。
        # 若侧边栏完成检测疑似失效（本课程进度表已有记录却一条都没识别到），再退回 progress.json。
        server_done = {l_["cid"] for l_ in lessons if l_.get("cid") and l_.get("done")}
        prog_done = {l_["cid"] for l_ in lessons if l_.get("cid") and l_["cid"] in self.done}
        if server_done or not prog_done:
            done_set = server_done
            basis = "服务器"
        else:
            done_set = prog_done
            basis = "本地进度"
        resume = None
        if lessons:
            for l_ in lessons:
                if l_.get("cid") and l_["cid"] not in done_set:
                    resume = l_
                    break
        if lessons and resume is None:
            log("进度表已覆盖本课程全部 %d 个已知课时，跳过该课程" % len(lessons))
            return
        if resume is not None and resume["cid"] != self._current_lesson_id():
            rname = name_by_id.get(resume["cid"], resume["cid"])
            tpl = getattr(self, "_study_tpl", "") or ""
            if tpl.startswith("http") and "chapterId={}" in tpl:
                self._goto(page, tpl.format(resume["cid"]))
            else:
                self._goto(page, resume.get("url", ""))
            for _ in range(6):
                page.wait_for_timeout(3000)
                if self._current_lesson_id() == resume["cid"]:
                    break
            log("断点续学：从第一个未完成课时开始: %s（依据%s完成状态 %d/%d）" % (rname, basis, len(done_set), len(lessons)))
        count = 0
        seen = set()
        course_start = time.time()
        while True:
            if time.time() - course_start > self.per_course_timeout:
                log("课程总时长超时，结束该课程")
                break
            cur = self._current_lesson_id()
            if not cur:
                log("无法识别当前课时，尝试点击下一节…")
                if not self._goto_next_lesson():
                    log("无法前进，结束课程")
                    break
                self._wait_lesson_change("", timeout=max(20, self.ended_grace))
                continue
            if self._lesson_locked():
                cur_name = name_by_id.get(cur, cur)
                cur_url = getattr(self, "_study_tpl", "").format(cur)
                if not cur_url.startswith("http"):
                    cur_url = page.url
                # 闯关模式：课时未开放通常意味着紧邻的前置课时在服务器端尚未真正完成。
                # 先回退补做前置课时，再回到本节重试；若补做后仍锁定，判定为教师端锁定并跳过。
                prev = None
                for i, l_ in enumerate(lessons):
                    if l_.get("cid") == cur:
                        prev = lessons[i - 1] if i > 0 else None
                        break
                tried = getattr(self, "_locked_fix_tried", None)
                if tried is None:
                    tried = set()
                    self._locked_fix_tried = tried
                fixed = False
                if prev and prev.get("cid") and prev["cid"] not in tried:
                    tried.add(prev["cid"])
                    pname = name_by_id.get(prev["cid"], prev["cid"])
                    log("该课时未开放（章节未开放），回退补做前置课时: %s" % pname)
                    purl = getattr(self, "_study_tpl", "").format(prev["cid"])
                    if not purl.startswith("http"):
                        purl = prev.get("url", "")
                    self._goto(page, purl)
                    page.wait_for_timeout(4000)
                    self._lesson_key = prev["cid"]
                    self._replayed = set()
                    self._initdata_quizzes = []
                    self._inserted_seeked = set()
                    self._answered_sig = {}
                    pc = self._process_current_page()
                    if pc and prev["cid"] not in self.done:
                        self.done[prev["cid"]] = time.strftime("%Y-%m-%d %H:%M:%S")
                        self._save_progress()
                    self._goto(page, cur_url)
                    page.wait_for_timeout(4000)
                    if not self._lesson_locked():
                        log("前置课时已补做，本课已开放，继续学习: %s" % cur_name)
                        fixed = True
                if fixed:
                    continue
                if prev and prev.get("cid") and prev["cid"] in tried:
                    log("前置课时已补做但本课仍未开放，判定为教师端锁定，跳过: %s" % cur_name)
                else:
                    log("该课时未开放（章节未开放），跳过: %s" % cur_name)
                seen.add(cur)
                count += 1
                self._lesson_key = cur
                # 直接尝试推进下一节
                arrow_ok = self._goto_next_lesson()
                new_id = None
                if arrow_ok:
                    new_id = self._wait_lesson_change(cur, timeout=max(30, self.ended_grace))
                if (not arrow_ok) or new_id is None or new_id == cur:
                    nxt = self._next_lesson_in_list(cur, lessons, seen)
                    if nxt is None:
                        log("锁定课时后无后续可用课时，结束课程")
                        break
                    nxt_id = nxt["cid"]
                    url = getattr(self, "_study_tpl", "").format(nxt_id)
                    if url.startswith("http"):
                        self._goto(page, url)
                    else:
                        self._goto(page, nxt["url"])
                    log("锁定课时已跳过，直连到下一课时: %s" % name_by_id.get(nxt_id, nxt_id))
                continue
            if cur in seen:
                log("检测到课时重复（%s），提前结束课程" % cur)
                break
            seen.add(cur)
            count += 1
            name = name_by_id.get(cur, "课时" + str(count))
            log("====== [%d/%s] 学习: %s ======" % (count, total if total else "?", name))
            self._lesson_key = cur
            self._replayed = set()
            self._initdata_quizzes = []
            self._inserted_seeked = set()
            self._answered_sig = {}
            completed = self._process_current_page()
            if completed:
                if cur not in self.done:
                    self.done[cur] = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._save_progress()
            else:
                log("本课未确认识别为完成(unfinishCount>0)，不记录进度: %s" % name)
            self._submitted = set()
            # 推进到下一节：先试箭头；箭头缺失或点击后课时未变化时，
            # 改用侧边栏课时列表直连跳转，避免整门课程提前结束。
            arrow_ok = self._goto_next_lesson()
            new_id = None
            if arrow_ok:
                new_id = self._wait_lesson_change(cur, timeout=max(30, self.ended_grace))
            if (not arrow_ok) or new_id is None or new_id == cur:
                nxt = self._next_lesson_in_list(cur, lessons, seen)
                if nxt is None:
                    if arrow_ok:
                        log("下一节未能加载/切换，且无后续课时，结束课程")
                    else:
                        log("已到达最后一节，课程完成 ✔")
                    break
                nxt_id = nxt["cid"]
                if arrow_ok:
                    log("箭头推进未生效，直连跳转到下一课时: %s" % (name_by_id.get(nxt_id, nxt_id)))
                else:
                    log("未找到下一节箭头（疑似末节/异常页），直连跳转到下一课时: %s" % (name_by_id.get(nxt_id, nxt_id)))
                url = getattr(self, "_study_tpl", "").format(nxt_id)
                if url.startswith("http"):
                    self._goto(page, url)
                else:
                    self._goto(page, nxt["url"])
                # 跳转后核验：id 已切换且真实内容已渲染（不满足则强制刷新/兜底重载恢复）
                if self._lesson_locked():
                    log("直连跳转到未开放课时，跳过: %s" % (name_by_id.get(nxt_id, nxt_id)))
                    continue
                if not self._verify_lesson_loaded(cur, page):
                    log("直连跳转后页面未渲染出内容，尝试强制恢复…")
                    if not self._recover_loaded_lesson(cur, page, url):
                        log("强制恢复失败，结束课程")
                        break

    # ---------------- 当前课时识别 / 顺序推进 ----------------
    def _current_lesson_id(self):
        # 优先从地址栏 chapterId 解析（导航/直连跳转后 DOM 可能滞后）
        try:
            u = self.page.url
            m = re.search(r"[?&]chapterId=(\d+)", u)
            if m:
                return m.group(1)
        except Exception:
            pass
        try:
            v = self.page.evaluate(
                """() => {
                  const el = document.querySelector('#chapterIdid');
                  if (el && el.value) return String(el.value);
                  const ifr = document.querySelector('iframe#iframe');
                  const src = ifr ? (ifr.getAttribute('src') || '') : '';
                  const m = src.match(/knowledgeid=(\\d+)/);
                  return m ? m[1] : '';
                }"""
            )
            return v or None
        except Exception:
            return None

    def _lesson_content_ready(self):
        """判断当前 studentstudy 页面是否已渲染出真实内容（侧边栏课时/任务点状态/知识卡 iframe）。

        故障课时特征：URL 的 chapterId 正确，但页面是空壳（无 #_studystate、无侧边栏、
        无知识卡 iframe），JS 全程未引导出内容。此函数用于核验是否真的加载到位。
        """
        try:
            v = self.page.evaluate(
                """() => {
                  const st = document.querySelector('#_studystate');
                  const stOk = !!st && (st.value || '').trim().length > 0;
                  const side = document.querySelectorAll('h4[id^="cur"], h5[id^="cur"]').length;
                  const ifr = document.querySelector('iframe#iframe');
                  const ifrOk = ifr && /knowledgeid=/.test(ifr.getAttribute('src') || '');
                  return (stOk ? 1 : 0) | (side > 0 ? 2 : 0) | (ifrOk ? 4 : 0);
                }"""
            )
            return bool(v and v > 0)
        except Exception:
            return False

    def _lesson_locked(self):
        """当前页面是否为服务端“章节未开放”锁定页（title=提交失败 / body含“章节未开放”等）。

        锁定页是服务器按课时返回的静态提示页：sidebar/iframe/_studystate 全空，
        任何 reload / 时间戳导航都无法改变其状态。检测后应快速跳过该课时而非空等/重试。
        """
        try:
            v = self.page.evaluate(
                """() => {
                  const t = (document.title || '');
                  const b = (document.body ? document.body.innerText : '');
                  const txt = b.slice(0, 3000);
                  if (/提交失败|校验失败/.test(t)) return 1;
                  if (/章节未开放|未开放|校验失败/.test(txt)) return 1;
                  const side = document.querySelectorAll('h4[id^="cur"], h5[id^="cur"]').length;
                  if (side === 0 && /温馨提示|提交失败/.test(txt)) return 1;
                  return 0;
                }"""
            )
            return bool(v)
        except Exception:
            return False

    def _verify_lesson_loaded(self, old_id, page, timeout=20):
        """跳转后核验：课时 id 已切换且页面渲染出真实内容。返回是否成功。"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                cur = self._current_lesson_id()
                if cur and cur != old_id and self._lesson_content_ready():
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(2000)
        cur = self._current_lesson_id()
        try:
            log("内容核验失败: old=%s new=%s ready_state=%s" % (
                old_id, cur, page.evaluate("document.readyState")))
        except Exception:
            pass
        return False

    def _recover_loaded_lesson(self, old_id, page, url, max_attempts=3):
        """空壳页恢复：强制刷新 / 用带时间戳参数的全新导航兜底，让页面 JS 引导出内容。"""
        for attempt in range(1, max_attempts + 1):
            log("恢复内容(%d/%d)：强制刷新页面…" % (attempt, max_attempts))
            try:
                page.reload(timeout=max(30000, int(self.wait_timeout * 1000)),
                            wait_until="domcontentloaded")
            except Exception as e:
                log("刷新异常:", e)
            # 刷新也可能命中同样的故障，必要时追加时间戳参数全新加载（绕过缓存/旧文档）
            if not self._verify_lesson_loaded(old_id, page, timeout=15):
                try:
                    sep = "&" if "?" in url else "?"
                    bust_url = url + sep + "_ts=" + str(int(time.time() * 1000))
                    log("刷新仍未渲染，改用全新导航(带时间戳)…")
                    self._goto(page, bust_url)
                    if self._verify_lesson_loaded(old_id, page, timeout=18):
                        return True
                except Exception:
                    pass
            else:
                return True
        return False

    def _goto_next_lesson(self):
        """点击当前 studentstudy 页可见的“下一节”右箭头。找不到(末节)返回 False。"""
        try:
            has = self.page.evaluate(
                """() => {
                  const els = Array.from(document.querySelectorAll('.orientationright'));
                  return !!els.find(e => e.getBoundingClientRect().width > 5 && /PCount.next/.test(e.getAttribute('onclick')||''));
                }"""
            )
            if not has:
                return False
            clicked = False
            # 优先用受信任的 locator 点击，PCount.next 可能校验真实用户手势
            try:
                loc = self.page.locator(".orientationright[onclick*='PCount.next']")
                if loc.count() > 0:
                    loc.first.click(timeout=2500, force=True)
                    clicked = True
            except Exception:
                pass
            if not clicked:
                self.page.evaluate(
                    """() => {
                      const els = Array.from(document.querySelectorAll('.orientationright'));
                      const el = els.find(e => e.getBoundingClientRect().width > 5 && /PCount.next/.test(e.getAttribute('onclick')||''));
                      if (el) el.click();
                    }"""
                )
            return True
        except Exception:
            return False

    def _switch_content_tab(self):
        """当前课时若有非视频内容卡（章节测验等，onclick 含 changeDisplayContent），
        切换到第一个非当前卡。返回是否成功切换。"""
        try:
            n = self.page.evaluate(
                """() => {
                  const tabs = Array.from(document.querySelectorAll('[onclick*="changeDisplayContent"]'));
                  const target = tabs.find(t => !/\\bcurrents?\\b/.test(t.className || '') && t.getBoundingClientRect().width > 0);
                  if (!target) return 0;
                  const idx = tabs.indexOf(target);
                  try { target.click(); } catch (e) {}
                  return idx + 1;
                }"""
            )
            if n:
                log("已切换到内容卡(第 %d 个卡，章节测验等)…" % n)
                return True
            return False
        except Exception as e:
            log("切换内容卡失败:", e)
            return False

    def _reenter_lesson(self):
        """内容丢失(弹窗/跳转)后重新进入当前课时。返回是否成功。"""
        cur = self._current_lesson_id() or self._lesson_key
        tpl = getattr(self, "_study_tpl", "") or ""
        if not cur or "chapterId={}" not in tpl:
            return False
        try:
            self._goto(self.page, tpl.format(cur))
            self.page.wait_for_timeout(3000)
            self._replayed = set()
            return True
        except Exception as e:
            log("重进本节失败:", e)
            return False

    def _wait_lesson_change(self, old_id, timeout=40):
        """等待内容切换到新的课时 id；处理可能出现的确认遮挡层。"""
        start = time.time()
        while time.time() - start < timeout:
            self.page.wait_for_timeout(2000)
            cur = self._current_lesson_id()
            if cur and cur != old_id:
                return cur
            # 可能弹出“确认离开/继续”遮挡层，尝试点击继续
            if time.time() - start > 6:
                try:
                    self.page.evaluate(
                        """() => {
                          const btns = Array.from(document.querySelectorAll('.checkDiv button, .popDiv button, .mypop button, .tipDiv .btn, .confirmBtn, .jumpBtn, [data-btntype]'));
                          const b = btns.find(x => x.getBoundingClientRect().width > 0 && /确定|继续|是|离开|前往/.test(x.innerText||''));
                          if (b) b.click();
                        }"""
                    )
                except Exception:
                    pass
        return None

    def _unfinish_count(self):
        """读取当前课时任务点未完成数(_studystate)。返回 int；读取失败返回 None。

        注意：#_studystate 反映的是“下一步要学”的课时任务点数。当前课时已全部完成时，
        它指向下一课时，返回的是下一课的任务点数（非 0）。因此判断“本课是否完成”应
        优先结合侧边栏服务端标记（_lesson_all_done_sidebar），而不要只依赖本函数==0。
        """
        try:
            v = self.page.evaluate(
                """() => {
                  const el = document.querySelector('#_studystate');
                  if (!el) return '';
                  const m = (el.value || '').match(/unfinishCount:(\\d+)/);
                  return m ? m[1] : '';
                }"""
            )
            return int(v) if v != "" else None
        except Exception:
            return None

    def _lesson_all_done_sidebar(self, cid=None):
        """侧边栏服务端标记：该课时全部任务点已完成。

        以课堂派侧边栏为准（真人视角）：课时行含蓝色完成圆点 span.roundpointStudent.blue、
        或带 done/succeed/finished 等完成类/已完成图标，且不再显示未完成任务点
        （无 jobUnfinishCount>0 / jobCount 计数）。用于 _process_current_page 的完成判定，
        避免 #_studystate 指向下一课时导致本已完成的课时被误判为未完成。
        """
        try:
            if not cid:
                cid = self._current_lesson_id()
            if not cid:
                return False
            cid = str(cid)
            v = False
            # 侧边栏异步渲染：完成蓝点/未完成任务标记可能稍晚出现。
            # 已确认未完成（jobCount 或 jobUnfinishCount>0）立即返回 False；
            # 找不到 h 或无标记时短时轮询，避免把已完成的课时误判为未完成而重看视频。
            budget = getattr(self, "sidebar_settle", 8.0)
            wait_internal = 0.7
            deadline = time.time() + budget
            first = True
            while first or time.time() < deadline:
                first = False
                r = self.page.evaluate(
                    """(cid) => {
                  const h = document.querySelector('#cur' + cid);
                  if (!h) return {st: 'noload', blue: false};
                  const pend = h.querySelector('input.jobUnfinishCount, span.jobCount');
                  if (pend) {
                    if (pend.tagName === 'SPAN') return {st: 'pending', blue: false};
                    const vv = (pend.getAttribute('value') || '').trim();
                    if (vv !== '' && vv !== '0') return {st: 'pending', blue: false};
                  }
                  const cell = h.closest('.ncells, .chapterCell, li, div') || h;
                  const cls = (cell.className || '') + ' ' + (h.className || '');
                  const blue = !!h.querySelector('span.roundpointStudent.blue')
                      || /(done|succeed|finished|completed|studied)/i.test(cls)
                      || !!(cell.querySelector('img[src*=\"jobfinished\"], img[src*=\"done\"], i.done, i.succ'));
                  return {st: blue ? 'done' : 'clear', blue: blue};
                }""",
                    cid,
                )
                st = (r or {}).get("st") if isinstance(r, dict) else None
                if st == "done":
                    v = True
                    break
                if st == "pending":
                    v = False
                    break
                if st == "noload":
                    time.sleep(wait_internal)
                    continue
                # 已加载但既无完成标记也无未完成计数 → 视为未完成
                v = False
                break
            return v
        except Exception:
            return False

    def _all_done_now(self):
        """当前课时是否已完成：unfinishCount==0（stuState 即为本课时）或侧边栏服务端已标记完成。"""
        uc = self._unfinish_count()
        if uc == 0:
            return True
        return self._lesson_all_done_sidebar()

    def _process_current_page(self):
        """在当前打开的 studentstudy 页面内处理视频与题目，直到完成或超时。

        返回值：True=本课确认真完成（unfinishCount 已清零或检测到完成标记）；
        False=因超时/无进展/异常等原因提前退出，任务点未清零，不能记作完成。
        """
        page = self.page
        # 服务端“章节未开放”锁定页：不视为本课任务，快速返回 False 让上层跳到下一节
        if self._lesson_locked():
            log("课时未开放（章节未开放），不处理本课")
            return False
        start = time.time()
        last_activity = start
        ended_since = None
        video_ended = False
        boot_deadline = time.time() + 120  # 内容引导期：最多等 120 秒让页面/iframe 把内容加载出来
        content_seen = False
        lost_since = None  # 曾看到内容但帧里突然找不到视频/题目 → 可能被弹窗/跳转打断，超过阈值则重进本节
        next_attempts = 0  # 本课内点“下一页”推进小节的次数上限
        post_next = None  # 点“下一页”后的加载缓冲期，期间不判空闲
        ended_video_url = None
        quiz_deadline = None  # 切到“章节测验”等内容卡后的作答时限；未切为 None
        # 任务点已全部完成 → 无需观看/作答，直接返回让上层点下一节
        uc = self._unfinish_count()
        if self._all_done_now():
            log("该课时任务点已完成(服务端已标记)，直接进入下一节")
            return True
        if uc is not None and uc > 0:
            log("该课时待完成任务点: %d" % uc)
        completed = False
        while True:
            try:
                now = time.time()
                if now - start > self.per_lesson_timeout:
                    if self._all_done_now():
                        log("本课处理超时但任务点已清零，视为完成")
                        completed = True
                    else:
                        log("本课处理超时且任务点未清零，不记完成")
                    break
                touched = 0
                for f in self._frames():
                    try:
                        touched += self.answer_and_submit(f)
                    except Exception:
                        pass
                vs = self._handle_video()
                # 是否看到了真实内容（视频存在或有题目被作答）
                if vs != "none" or touched:
                    content_seen = True
                if touched:
                    last_activity = now
                if vs == "playing" and not video_ended:
                    last_activity = now
                if vs == "ended" and not video_ended:
                    # 粘性标记：一旦看完就固定等待计时，后续即使返回 playing/滚动到别处也不重置，
                    # 否则 ended_grace 永远等不到，整节课会无限挂起
                    video_ended = True
                    ended_since = now
                    ended_video_url = self._cur_video_url
                    log("视频已看完，等待页面推进/测验…")
                done_flag = False
                for f in self._frames():
                    try:
                        if f.evaluate(NODE_FINISH_JS):
                            done_flag = True
                            break
                    except Exception:
                        pass
                if done_flag:
                    log("检测到本节已完成")
                    completed = True
                    break
                if video_ended and ended_since is not None:
                    waited = now - ended_since
                    for _t in (30, 60, 90):
                        if 0 <= waited - _t < self.poll:
                            log("等待页面推进 %ds (vs=%s)…" % (int(waited), vs))
                    # 本课完成以任务点清零为准：unfinishCount==0 才是真完成，可交给上层点下一节
                    uc_now = self._unfinish_count()
                    if self._all_done_now():
                        log("任务点已全部完成(服务端已标记)，本课完成")
                        completed = True
                        break
                    # 视频已看完但本课时仍剩任务点 → 若有“章节测验”等内容卡先切过去作答，再考虑点“下一页”
                    if uc_now is not None and uc_now > 0 and waited > self.kp_grace:
                        if (
                            quiz_deadline is None
                            and next_attempts < self.kp_next_max
                            and (vs == "none" or vs == "ended")
                        ):
                            if self._switch_content_tab():
                                quiz_deadline = time.time() + 75
                                last_activity = now
                                log("等待内容卡作业帧加载并作答…")
                                page.wait_for_timeout(3000)
                                continue
                        if quiz_deadline is not None:
                            if now < quiz_deadline:
                                page.wait_for_timeout(int(self.poll * 1000))
                                continue
                            log("内容卡未消费任务点，回退点下一页…")
                            quiz_deadline = None
                        if next_attempts < self.kp_next_max and (vs == "none" or vs == "ended"):
                            log("本课时仍剩 %d 个任务点，点击下一页继续…" % uc_now)
                            clicked = self._goto_next_lesson()
                            if clicked:
                                next_attempts += 1
                                log("已点击下一页，等待新内容加载…")
                                self._replayed = set()
                                video_ended = False
                                ended_since = None
                                post_next = time.time() + 60
                                last_activity = now
                                page.wait_for_timeout(3000)
                                continue
                    if waited > self.ended_grace:
                        if self._all_done_now():
                            log("视频结束 %.0f 秒后无新内容，任务点已清零，本课完成" % self.ended_grace)
                            completed = True
                        else:
                            log("视频结束 %.0f 秒后无新内容，任务点未清零，不记完成" % self.ended_grace)
                        break
                else:
                    # 刚点击“下一页”进入新小节、内容尚在加载 → 抑制空闲判定
                    if post_next is not None and now < post_next:
                        if vs != "none" and self._cur_video_url and self._cur_video_url != ended_video_url:
                            log("已进入本课下一小节，继续观看…")
                            post_next = None
                        page.wait_for_timeout(int(self.poll * 1000))
                        continue
                    # 纯图文小节等自动登记后也可能出现清零 → 直接算完成
                    uc_now = self._unfinish_count()
                    if self._all_done_now():
                        log("任务点已全部完成(服务端已标记)，本课完成")
                        completed = True
                        break
                    # 一直没看到视频/题目但有未完成任务点 → 可能有“章节测验”等非视频卡，切过去作答
                    if (
                        quiz_deadline is None
                        and not content_seen
                        and uc_now is not None
                        and uc_now > 0
                        and now > boot_deadline
                    ):
                        if self._switch_content_tab():
                            quiz_deadline = time.time() + 75
                            last_activity = now
                            log("等待内容卡作业帧加载并作答…")
                            page.wait_for_timeout(3000)
                            continue
                    if quiz_deadline is not None:
                        if now < quiz_deadline:
                            page.wait_for_timeout(int(self.poll * 1000))
                            continue
                        log("内容卡未消费任务点，放弃本节…")
                        quiz_deadline = None
                    # 内容还没加载出来时不要因空闲判死，耐心等
                    if not content_seen and now < boot_deadline:
                        page.wait_for_timeout(int(self.poll * 1000))
                        continue
                    if content_seen and vs == "none" and not touched:
                        # 曾看到内容但现在任何帧里都找不到视频/题目：
                        # 多半是被登录警示等弹窗或页面跳转打断，尝试重新进入本节继续看/作答
                        if lost_since is None:
                            lost_since = now
                        if now - lost_since > 20:
                            log("内容丢失(可能被登录警示弹窗打断)，重新进入本节…")
                            if self._reenter_lesson():
                                lost_since = None
                                content_seen = False
                                boot_deadline = time.time() + 120
                                page.wait_for_timeout(2000)
                                continue
                            log("重新进入失败，任务点未清零，不记完成")
                            break
                    else:
                        lost_since = None
                    if now - last_activity > self.idle_timeout:
                        if self._all_done_now():
                            log("本课无进展但任务点已清零，视为完成")
                            completed = True
                        else:
                            log("本课无进展且任务点未清零，不记完成")
                        break
                page.wait_for_timeout(int(self.poll * 1000))
            except Exception as e:
                log("本课处理异常:", e)
                traceback.print_exc()
                break
        # 结束前最后一轮作答/提交
        for f in self._frames():
            try:
                self.answer_and_submit(f)
            except Exception:
                pass
        return completed

    def _extract_full_lessons(self):
        """从 studentstudy 侧边栏 h4/h5(id=cur<chapterId>) 提取全部课时真实 id。"""
        try:
            data = self.page.evaluate(
                """() => {
                  const seen = new Set();
                  const out = [];
                  document.querySelectorAll('h4[id^="cur"], h5[id^="cur"]').forEach(h => {
                    const cid = (h.id || '').replace(/^cur/, '');
                    if (!cid || seen.has(cid)) return;
                    seen.add(cid);
                    // 完成状态：该课时单元格是否带完成标记
                    const cell = h.closest('.ncells, .chapterCell, li, div') || h;
                    const cls = (cell.className || '') + ' ' + (h.className || '');
                    const done = /(done|succeed|finished|completed|studied)/i.test(cls)
                        || !!h.querySelector('span.roundpointStudent.blue')
                        || !!(cell.querySelector('img[src*="jobfinished"], img[src*="done"], i.done, i.succ'));
                    const name = (h.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
                    out.push({cid: cid, name: name, done: done});
                  });
                  return out;
                }"""
            )
        except Exception as e:
            log("提取侧边栏课时失败:", e)
            return []
        if not data:
            return []
        url = self.page.url
        m_c = re.search(r"[cC]ourseId=(\d+)", url)
        m_z = re.search(r"clazzid=(\d+)", url)
        m_e = re.search(r"enc=([^&\"]+)", url)
        course_id = m_c.group(1) if m_c else ""
        clazzid = m_z.group(1) if m_z else ""
        enc = m_e.group(1) if m_e else ""
        lessons = []
        tpl = getattr(self, "_study_tpl", "") or ""
        for d in data:
            if tpl.startswith("http") and "chapterId={}" in tpl:
                lessons.append({"cid": d["cid"], "name": d["name"], "url": tpl.format(d["cid"]), "done": d.get("done")})
                continue
            q = "chapterId=%s&courseId=%s&clazzid=%s" % (d["cid"], course_id, clazzid)
            if enc:
                q += "&enc=" + enc
            lessons.append({"cid": d["cid"], "name": d["name"], "url": "https://mooc1-1.chaoxing.com/mooc-ans/mycourse/studentstudy?" + q, "done": d.get("done")})
        return lessons

    def _next_lesson_in_list(self, cur, lessons, seen):
        """在侧边栏课时列表里找 cur 的下一个课时对象；cur 不在列表时取第一个未看过课时。"""
        try:
            cids = [l_.get("cid") for l_ in lessons if l_.get("cid")]
            i = cids.index(cur) if cur in cids else -1
            for cid in cids[i + 1:]:
                if cid not in seen:
                    return next((l_ for l_ in lessons if l_.get("cid") == cid), None)
            return None
        except Exception:
            return None

    def _study_lesson_links(self, course, links):
        """兜底：用课程首页目录链接逐个刷（老逻辑）。"""
        log("该课程共 %d 个知识点/测验" % len(links))
        for i, lk in enumerate(links, 1):
            name = lk.get("name") or ("知识点" + str(i))
            url = self._resolve(lk.get("href", ""))
            if not url:
                continue
            if url in self.done:
                log("[%d/%d] 已在进度表，跳过: %s" % (i, len(links), name))
                continue
            if self.skip_done and lk.get("done"):
                log("[%d/%d] 任务点已完成，跳过: %s" % (i, len(links), name))
                continue
            log("[%d/%d] 开始学习: %s" % (i, len(links), name))
            try:
                ok = self.process_knowledge(url)
            except Exception as e:
                log("处理该节出错，继续下一节:", e)
                traceback.print_exc()
                ok = False
            if ok:
                self.done[url] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save_progress()
            else:
                log("本课未确认识别为完成(unfinishCount>0)，不记录进度: %s" % name)
            self._submitted = set()

    # ---------------- 单节处理 ----------------
    def process_knowledge(self, url):
        page = self.page
        log("进入章节页:", url)
        self._goto(page, url)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
        return self._process_current_page()

    # ---------------- 视频 ----------------
    def _handle_video(self):
        """返回 'none' / 'playing' / 'ended'。

        纯真人模拟：1 倍速真实观看。
        - 首次遇到某视频：像真人一样点击播放按钮，并从 0 开始完整观看（保证插入题按时间点自然弹出作答、
          也保证服务端记录完整真实时长，避免快速完成/跳过被检测封号）。
        - 之后保持 1 倍速播放；播完自然结束。
        """
        rate = max(1.0, self.video_rate)
        self._cur_video_url = None
        for f in self._frames():
            try:
                info = f.evaluate(VIDEO_PROBE_JS)
            except Exception:
                continue
            if not info or not info.get("found"):
                continue
            cur = info.get("cur") or 0
            dur = info.get("dur") or 0
            key = f.url[:70]
            self._cur_video_url = f.url
            # 已播完：不调用播放JS（否则 video-js 会把结束的视频重新从头播，任务点永远登记不了）
            if dur and dur > 5 and cur >= dur - 1:
                return "ended"
            if key not in self._replayed:
                # 真人式点击播放按钮（受信任点击，满足浏览器用户手势/自动播放策略）
                try:
                    if f.locator(".vjs-big-play-button, .video-js, .big-play-button").count() > 0:
                        f.locator(".vjs-big-play-button, .video-js, .big-play-button").first.click(timeout=2500, force=True)
                except Exception:
                    pass
                try:
                    f.evaluate(PLAYER_RESTART_JS.replace("__RATE__", repr(rate)))
                    self._replayed.add(key)
                    log("开始以 1 倍速真实观看本节视频…")
                except Exception:
                    pass
                return "playing"
            try:
                f.evaluate(PLAYER_PLAY_JS.replace("__RATE__", repr(rate)))
            except Exception:
                pass
            if dur and dur > 5 and cur >= dur - 1:
                return "ended"
            return "playing"
        return "none"

    def _next_pending_inserted(self, dur):
        """从捕获的 initdata 中找当前视频里尚未 seek 作答的插入题。"""
        if not self._initdata_quizzes:
            return None
        for q in sorted(self._initdata_quizzes, key=lambda x: x.get("startTime", 0)):
            st = q.get("startTime", 0)
            if st is None or st < 2 or st >= dur:
                continue
            if q.get("answered"):
                continue
            if round(st * 10) in self._inserted_seeked:
                continue
            return q
        return None

    # ---------------- 答题 ----------------
    def answer_and_submit(self, frame):
        try:
            qs = frame.evaluate(EXTRACT_QUESTIONS_JS)
        except Exception:
            return 0
        if not qs:
            # 疑似出现题目却未被标准结构识别 → 转储一次该帧 HTML 便于修正选择器
            self._answered_sig.pop((self._lesson_key, frame.url or ""), None)
            try:
                if (
                    frame.evaluate(PROBE_QUESTION_JS)
                    and self._debug_dump_keys.get(self._lesson_key) != frame.url[:60]
                ):
                    self._debug_dump_keys[self._lesson_key] = frame.url[:60]
                    self._dump_html(frame, "popup_" + self._lesson_key)
            except Exception:
                pass
            # 作业页各题已作答（input:checked 后 EXTRACT 会跳过题目）但“确认提交”弹窗待点：
            # touched==0 走不到 CLICK_CONFIRM_JS，这里补点“确定”完成提交
            try:
                if frame.evaluate(WORK_DETECT_JS):
                    if frame.evaluate(CLICK_CONFIRM_JS):
                        log("已点击作业确认提交弹窗的确定")
            except Exception:
                pass
            return 0
        # 题干已出现但选项/填空尚未渲染（弹窗动画期）→ 转储一次并短等待后重抽取，
        # 避免"选项 0 项还空提交"导致 回答错误/回看 卡死
        if all((not q.get("options")) and q.get("blankCount", 0) == 0 for q in qs):
            try:
                dk = "nopts_" + self._lesson_key
                if self._debug_dump_keys.get(dk) != frame.url[:60]:
                    self._debug_dump_keys[dk] = frame.url[:60]
                    self._dump_html(frame, "nopts_" + self._lesson_key)
            except Exception:
                pass
            try:
                time.sleep(1.5)
                qs = frame.evaluate(EXTRACT_QUESTIONS_JS)
            except Exception:
                pass
        if qs:
            sig_key = (self._lesson_key, frame.url or "")
            sig = tuple((q.get("type"), (q.get("stem") or "")[:60]) for q in qs)
            prev = self._answered_sig.get(sig_key)
            if prev and prev[0] == sig:
                cnt = prev[1] + 1
                self._answered_sig[sig_key] = (sig, cnt)
                if cnt >= 3:
                    log("同一组题连续 %d 轮作答均未消耗，停止作答本帧（疑似作业/回顾页死循环）" % cnt)
                    if self.debug_html:
                        self._dump_html(frame, "loop_" + self._lesson_key)
                    return 0
            else:
                self._answered_sig[sig_key] = (sig, 1)
        touched = 0
        for q in qs:
            if q.get("blankCount", 0) == 0 and not q.get("options"):
                continue
            ans = self._solve(frame, q)
            if ans is None:
                continue
            self._apply(frame, q, ans)
            touched += 1
        if touched == 0:
            # 无可作答项（选项/填空仍未渲染或未能求解）→ 不提交空答案，等下一轮轮询重试
            return 0
        try:
            frame.evaluate(CLICK_CONFIRM_JS)
        except Exception:
            pass
        try:
            iswork = frame.evaluate(WORK_DETECT_JS)
        except Exception:
            iswork = False
        if iswork:
            try:
                frame.evaluate(SAVE_WORK_JS)
            except Exception:
                pass
            key = frame.url or self.page.url
            if key not in self._submitted:
                try:
                    if self._submit_work_click(frame):
                        self._submitted.add(key)
                        touched += 1
                        log("已点击作业提交按钮(key=%s)" % key[-60:])
                except Exception:
                    pass
            elif self.debug_html:
                self._dump_html(frame, "submitted")
        # 视频内插入题：作答后点"提交"，显示对错后点"继续"
        try:
            frame.evaluate(VIDEOQUIZ_SUBMIT_JS)
        except Exception:
            pass
        return touched

    def _try_answer_generic(self, frame):
        """兜底：对非标准结构的题目弹窗（视频内插入题等），点第一个选项并点确定/提交。"""
        acted = 0
        try:
            n = frame.evaluate(GENERIC_POPUP_JS) or 0
            if n > 0:
                acted += int(n)
                log("已用兜底方式点选弹窗选项 (%d 处)" % int(n))
        except Exception:
            pass
        if acted:
            try:
                frame.evaluate(CLICK_CONFIRM_JS)
            except Exception:
                pass
            try:
                self._submit_work_click(frame)
            except Exception:
                pass
        return acted

    def _solve(self, frame, q):
        qtype = q.get("type", "single")
        stem = q.get("stem") or "（该题无文本，请根据选项作答）"
        options = q.get("options") or []
        # 图片选项 OCR
        if self.ocr.enabled:
            for o in options:
                for src in o.get("images") or []:
                    txt = self._ocr_image(frame, src)
                    if txt:
                        o["text"] = (o["text"] + " " + txt).strip() if o.get("text") else txt

        def try_ai(fn, fb_kind):
            try:
                return fn()
            except Exception as e:
                log("AI 不可用，使用兜底作答(%s): %s" % (fb_kind, str(e)[:80]))
                if fb_kind == "click":
                    return {"kind": "click", "letters": self.ai.fallback(qtype, options)}
                return {"kind": "fill", "values": self.ai.fallback(qtype, blank_count=q.get("blankCount", 1))}

        if qtype == "judge":
            return try_ai(lambda: {"kind": "click", "letters": [self.ai.solve_judge(stem)]}, "click")
        if qtype == "multi":
            return try_ai(lambda: {"kind": "click", "letters": self.ai.solve_choice(stem, options, multi=True)}, "click")
        if qtype == "fill":
            return try_ai(lambda: {"kind": "fill", "values": self.ai.solve_fill(stem, q.get("blankCount", 1))}, "fill")
        if qtype == "text":
            return try_ai(lambda: {"kind": "fill", "values": [self.ai.solve_text(stem)]}, "fill")
        # 单选（默认）
        return try_ai(lambda: {"kind": "click", "letters": [self.ai.solve_choice(stem, options, multi=False)]}, "click")

    def _apply(self, frame, q, answer):
        try:
            if answer["kind"] == "click":
                n = frame.evaluate(ANSWER_CLICK_JS, {"idx": q["idx"], "letters": answer["letters"]})
                log("作答[%s] 第%d题 选 %s (%d项)" % (q.get("type"), q.get("idx") + 1, answer["letters"], n))
            else:
                n = frame.evaluate(ANSWER_FILL_JS, {"idx": q["idx"], "values": answer["values"]})
                log("作答[%s] 第%d题 填 %s" % (q.get("type"), q.get("idx") + 1, answer["values"]))
        except Exception as e:
            log("答题点击失败:", e)
            if self.debug_html:
                self._dump_html(frame, "answer_fail")

    def _ocr_image(self, frame, src):
        try:
            if src.startswith("data:"):
                _, b64 = src.split(",", 1)
                raw = base64.b64decode(b64)
            else:
                if src.startswith("//"):
                    src = "https:" + src
                data = frame.evaluate(OCR_FETCH_JS, src)
                raw = bytes(data)
            text = self.ocr.ocr(raw)
            if text:
                log("OCR 识别图片题:", text[:80].replace("\n", " "))
            return text
        except Exception as e:
            log("OCR 失败:", e)
            return ""

    def _dump_html(self, frame, tag):
        try:
            html = frame.content()
            fn = "debug_%s_%s.html" % (tag, int(time.time()))
            with open(fn, "w", encoding="utf-8") as f:
                f.write(html)
            log("已保存调试 HTML:", fn)
        except Exception:
            pass

# 学习通课程自动学习脚本（视频 + AI 答题 + OCR + 真实点击提交）

自动辅助完成学习通（超星）课程的学习任务：自动登录 → 遍历课程章节 → 播放视频 → 作答课程中的插入题、章节测验、作业 → 图片题 OCR 识别 → **真实鼠标点击提交作业** → 断点续学。支持 **麒麟 Linux** 和 **Windows** 运行。

> ⚠️ 仅供个人学习使用。请遵守平台规则与学校规定，因使用本脚本造成的后果自负。

---

## 功能特性

- ✅ **自动登录**：账号密码登录；会话持久化到 `login_state.json`，下次直接复用，避免频繁验证码/扫码
- ✅ **自动遍历课程**：支持多课程（`course_names`），逐个知识点自动学习
- ✅ **视频两种模式**：
  - `watch`（当前服务器默认）：**挂机模式**，真实播放 + 可调倍速（推荐 1.0，最安全）
  - `fast`：快速完成模式，直接把进度拉到结尾并触发完成（速度快，有一定风险）
- ✅ **自动作答**：单选 / 多选 / 判断 / 填空 / 简答，接入 DeepSeek（OpenAI 兼容接口，可换智谱/通义）
- ✅ **AI 熔断降级**：AI 服务不可用时自动熔断 5 分钟并走兜底答案，保证作业能提交、任务点能解锁，不会每题卡几分钟
- ✅ **课程插入题弹窗**：自动识别作答并点"确定"继续
- ✅ **章节测验 / 作业页**：自动答完全部题目 → 保存答案 → **Playwright 真实点击提交按钮**
- ✅ **图片题 OCR**：RapidOCR 识别图片题/图片选项（中文友好、轻量）
- ✅ **进度断点续学**：`progress.json` 记录已完成知识点，中断后可继续
- ✅ **真实点击提交（关键修复）**：使用 `locator.click()` 触发浏览器原生受信鼠标事件，生成有效加密参数（`pos`/`rd`），解决作业提交失败问题（详见下文"作业提交机制"）
- ✅ **异常现场留档**：遇到意外 UI 保存 `debug_*.html`，便于排查

---

## 部署架构

本项目采用 **本地开发 + 虚拟机/服务器部署** 的方式：

```
本机（Windows，开发/验证）
   │  ── SSH (YOUR_SERVER_IP, root/YOUR_PASSWORD) ──
   ▼
麒麟 Linux 虚拟机（实际运行环境）
   └─ /root/chaoxing_auto/
        ├─ chaoxing.py   # 核心逻辑（已部署修复版本）
        ├─ config.json   # 实际运行配置（账号、课程、AI key 等）
        ├─ venv/         # Python 虚拟环境
        └─ 日志/进度文件
```

> 部署要点：本地与服务器 `chaoxing.py` 必须保持字节一致（本地修改后重新上传），否则线上行为与测试不符。

---

## 目录结构

```
chaoxing_auto/
├── config.example.json   # 配置模板（复制为 config.json 使用）
├── config.json           # 实际配置（含账号密码，勿外传）
├── main.py               # 入口（python main.py [--course X] [--headless]）
├── chaoxing.py           # 学习通核心逻辑（登录/学习/答题/提交）
├── ai.py                 # AI 答题（DeepSeek 等 OpenAI 兼容接口 + 熔断降级）
├── ocr.py                # 图片题 OCR
├── requirements.txt
├── run_win.bat           # Windows 一键启动
├── run_kylin.sh          # 麒麟/Linux 一键启动（自动建 venv、探测显示器）
├── start_bot.sh          # systemd 后台启动（与 SSH 会话分离，失败自动重启）
├── kill_bot.sh           # 停止后台任务
├── replay_work.py        # 单课时重放测试（验证提交链路）
├── progress.json         # 断点进度（自动生成）
├── login_state.json      # 登录会话（自动生成）
├── work_submit_trace.log # 作业提交明细（自动记录，排查提交失败的关键日志）
└── *.log / debug_*.html  # 运行日志 / 异常现场
```

---

## 一、安装

### 麒麟 Linux 虚拟机

```bash
cd /root/chaoxing_auto

# 1. 系统依赖
sudo apt update
sudo apt install -y python3 python3-pip python3-venv

# 2. 虚拟环境 + Python 依赖
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 3. 安装 Playwright 浏览器内核（需联网；自动补装系统库，需 root）
./venv/bin/playwright install chromium --with-deps
```

> - 国内网络慢可配置 pip 与 playwright 镜像源。
> - `rapidocr` 装不上（系统过旧）时，可只装 `playwright requests pillow`，并把 `config.json` 中 `ocr.enabled` 改为 `false`（仅影响图片题）。
> - 运行需要图形环境：无头服务器请在脚本内用 `DISPLAY` 指向 Xvfb 或直接 `headless: true`（但登录阶段看不到浏览器）。

### Windows 11

```bat
cd chaoxing_auto
pip install -r requirements.txt
playwright install chromium
```

---

## 二、配置详解（config.json）

复制模板并编辑：

```bash
cp config.example.json config.json   # Windows: copy config.example.json config.json
```

### 账号与登录

| 配置项 | 说明 |
|---|---|
| `username` / `password` | 学习通账号密码。留空则运行时会弹浏览器手动扫码登录 |
| `headless` | 是否无头。`false` 时能看到浏览器（登录扫码需要）；服务器无人值守可设 `true` |
| `browser` | 浏览器内核，默认 `chromium` |
| `state_file` | 登录会话文件（默认 `login_state.json`），登录成功后复用，减少重复验证码 |

### 课程选择

| 配置项 | 说明 |
|---|---|
| `course_name` | 课程名称关键字（单个），留空自动选第一个课程 |
| `course_names` | 课程名称关键字（多个，数组），会同时处理多个匹配课程。与 `course_name` 二选一即可 |
| `max_courses` | 最多处理的课程数（默认 3；服务器配置为 5） |
| `skip_done` | 已完成知识点直接跳过（默认 true） |

### 视频播放

| 配置项 | 说明 |
|---|---|
| `finish_mode` | `watch` 挂机真实播放（当前服务器默认，安全）/ `fast` 快速完成模式 |
| `video_rate` | 倍速。`watch` 模式建议 `1.0`~`2.0`；`fast` 模式可用 `16` |
| `fast_jump` | fast 模式下是否直接跳结尾（默认 true） |
| `poll_interval` | 播放状态轮询间隔秒数（默认 2.5，服务器 3.0） |
| `idle_timeout` | 页面无响应判定超时秒数（默认 25，服务器 30） |
| `ended_grace` | 视频播完后宽限等待秒数，等平台确认记录（默认 120） |
| `kp_grace` | 知识点切换宽限秒数（默认 30） |
| `kp_next_max` | 同一知识点最多尝试进入次数（默认 10） |

### 超时控制

| 配置项 | 说明 |
|---|---|
| `wait_timeout` | 通用等待元素超时（默认 60） |
| `per_knowledge_timeout` | 单个知识点最长耗时（默认 900 秒），超时跳过防止卡死 |
| `per_lesson_timeout` | 单节课/课时最长耗时（服务器 5400 秒 = 1.5 小时） |
| `per_course_timeout` | 单课程最长耗时（服务器 100000 秒），防止多课程无限跑 |

### AI 答题

```json
"ai": {
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-xxxxxxxx",
  "model": "deepseek-chat",
  "max_retries": 3,
  "knowledge_hint": "Linux操作系统（麒麟）课程",
  "proxy": "http://192.168.89.1:7897"
}
```

| 配置项 | 说明 |
|---|---|
| `base_url` | DeepSeek 默认 `https://api.deepseek.com/v1`；智谱/通义/OpenAI 中转换成对应地址 |
| `api_key` | **DeepSeek API key（必填才能答题）**。申请：<https://platform.deepseek.com>，充值 1-5 元即可 |
| `model` | `deepseek-chat`（或 `deepseek-reasoner`） |
| `max_retries` | AI 请求重试次数（默认 3，按 2/5/10/20 秒退避） |
| `knowledge_hint` | 课程背景提示，帮助 AI 回答更贴合课程内容 |
| `proxy` | 可选 HTTP 代理。**注意：当前服务器 `api.deepseek.com` 直连不通，必须走代理；若代理失效会触发熔断降级，题目只能靠兜底作答** |

### OCR

| 配置项 | 说明 |
|---|---|
| `ocr.enabled` | 是否开启图片题 OCR（默认 true） |

### 调试与进度

| 配置项 | 说明 |
|---|---|
| `debug_html` | 遇到异常 UI 是否保存 `debug_*.html`（默认 false，排查时打开） |
| `progress_file` | 进度文件（默认 `progress.json`） |

---

## 三、启动与停止

本项目提供了多个启动/停止脚本，**按场景选择**。下面先给一个"怎么选"的速查表，再逐个脚本详细说明。

### 3.0 脚本速查表

| 脚本 | 平台 | 运行方式 | 适用场景 |
|---|---|---|---|
| `run_kylin.sh` | 麒麟/Linux | 前台 | 初次部署 + 日常手动运行，**最常用** |
| `start_bot.sh` | Linux | 后台(systemd) | 无人值守、SSH 断开也不停、崩溃自动重启 |
| `kill_bot.sh` | Linux | — | 停止后台运行进程 |
| `run_win.bat` | Windows | 前台 | Windows 上双击一键运行 |
| `main.py` | 全平台 | — | 直接调 Python 入口，可加参数（高级用法） |

---

### 3.1 `run_kylin.sh` —— 麒麟/Linux 前台启动（最常用）

> ⚠️ 脚本名是 `run_kylin.sh`（不是 `run.kylin.sh`）。首次使用前请确认脚本有执行权限。

**一句话作用**：一键完成"进目录 → 补配置文件 → 探测显示器 → 建虚拟环境装依赖 → 启动脚本"。

**逐步骤说明**（对应脚本内容）：

```bash
cd "$(dirname "$0")"          # ① 自动切到脚本所在目录，从任意路径执行都行
if [ ! -f config.json ]       # ② 没有 config.json 就复制模板，并提醒你去填账号
then cp config.example.json config.json
fi
# ③ 探测图形显示器（:0 / :1），有就设为有头模式，便于扫码/验证码
# ④ 首次运行自动建 venv 并安装依赖（以后运行会跳过）
exec ./venv/bin/python main.py  # ⑤ 真正启动
```

**用法**：

```bash
cd /root/chaoxing_auto
bash run_kylin.sh
```

**关键提示**：

1. **首次运行前**，先手动把 `config.json` 里的 `username`/`password`/`ai.api_key` 填好，否则脚本只会复制一份空模板，答题和登录都会失败。
2. **依赖安装**：脚本只在 `venv` 目录不存在时才装依赖。若已改过 `requirements.txt` 需要重装，先删掉 `venv` 或手动执行：
   ```bash
   ./venv/bin/pip install -r requirements.txt
   ```
3. **无图形界面**（SSH 连到无桌面服务器）：`run_kylin.sh` 探测不到显示器，会保持无头启动，但登录扫码看不到浏览器。此时建议改用 `start_bot.sh`（后台无头），并确保 `config.json` 里 `headless: true` 且账号密码已填好。
4. **前台进程会随 SSH 断开而终止**：若只是临时试跑、观察日志，前台很直观；若要长时间挂机，用下一节的 `start_bot.sh`。

---

### 3.2 `start_bot.sh` —— 后台运行（无人值守，推荐）

> ⚠️ 用 systemd 启动，与 SSH 会话完全分离：**关掉终端/断开 SSH 都不会停**，进程崩溃还会自动重启。

**一句话作用**：把 `main.py` 交给 systemd 后台运行，日志写到 `/root/chaoxing_run.log`，失败 15 秒后自动重启。

**用法**：

```bash
cd /root/chaoxing_auto
bash start_bot.sh
# 看到 STARTED_VIA_SYSTEMD 即启动成功
```

**启动后的日常操作**：

```bash
# 查看运行状态（是否 active、最近日志）
systemctl status cxbot

# 实时跟看日志（学习进度、答题、提交都在这里）
tail -f /root/chaoxing_run.log

# 停止后台任务
systemctl stop cxbot
# 或直接用 kill_bot.sh
```

**注意**：

1. 脚本里写死了工作目录 `/root/chaoxing_auto`，如果项目不在这个目录，需要手动改脚本第 5 行和第 6 行。
2. 启动前会自动 `systemctl stop cxbot`，避免重复启动多个实例。
3. 若改了 `config.json` 或代码，需要先 `systemctl stop cxbot` 再重新 `bash start_bot.sh`。

---

### 3.3 `kill_bot.sh` —— 停止后台任务

**一句话作用**：强制结束 `venv/bin/python main.py` 进程。

**用法**：

```bash
cd /root/chaoxing_auto
bash kill_bot.sh
# 输出 killing: <pid> 或 no running bot
```

> 仅能杀掉前台/直接启动的 `main.py`。若是 `start_bot.sh` 启动的（systemd 托管），用 `systemctl stop cxbot` 更可靠（否则 systemd 会自动拉起）。

---

### 3.4 `run_win.bat` —— Windows 一键启动

**一句话作用**：Windows 双击即可——自动补 `config.json`、安装依赖、启动脚本。

**用法**：在 `chaoxing_auto` 目录下双击 `run_win.bat`（或命令行 `run_win.bat`）。

**逐步骤说明**：

```bat
chcp 65001 >nul              # 切 UTF-8 编码，避免中文乱码
cd /d %~dp0                  # 切到脚本所在目录
if not exist config.json copy config.example.json config.json  # 补配置模板
python -m pip install -r requirements.txt -q   # 装依赖
python main.py               # 启动
pause                        # 结束后暂停，便于看报错
```

---

### 3.5 直接运行 `main.py`（高级用法）

所有脚本最终都是调用 `main.py`。可手动加参数：

```bash
./venv/bin/python main.py                          # 使用 config.json
./venv/bin/python main.py --course "网络空间安全"    # 只处理名称含关键字的课程
./venv/bin/python main.py --headless                # 无头模式
./venv/bin/python main.py --config config.vm.json   # 指定其他配置文件
```

参数说明：

| 参数 | 作用 |
|---|---|
| `--config` | 指定配置文件路径（默认 `config.json`） |
| `--course` | 指定课程名称关键字，覆盖配置里的 `course_name` |
| `--headless` | 无头模式（覆盖配置里的 `headless`） |

---

## 四、运行日志与进度

- **实时日志**：前台直接看终端；后台看 `/root/chaoxing_run.log`。
- **进度文件** `progress.json`：记录每个已完成的知识点 chapterId 和完成时间。想重新学习某个知识点，可删除对应键或整个文件。
- **提交明细** `work_submit_trace.log`：每次作业提交的完整请求与响应（`POST` 参数 + `RESP` 响应）。**排查作业是否提交成功看这个文件**：
  - `RESP: {"msg":"success!","stuStatus":4,...,"status":true}` → 提交成功
  - `RESP: {"msg":"作业提交失败，","status":false}` → 提交失败（原因见下节）

---

## 五、作业提交机制（关键修复，请知悉）

### 背景：为什么之前作业提交总失败？

学习通作业提交页的按钮走 `onclick="btnBlueSubmit()"`，内部调用 `validateTimeNew(...)` → 请求 `/work/validate` → `form1submit()` 确认提交 → `version()` 中通过 `getEnc()` 生成加密参数。

旧代码用 JS 合成 `element.click()` 触发点击，生成的是 **非受信 MouseEvent（isTrusted=false）且坐标 (0,0)**，导致 `getEnc()` 计算出**无效的 `pos` / `rd` 值**，服务端校验失败 → `{"msg":"作业提交失败，","status":false}`。

### 修复方案：Playwright 真实点击

- 提交按钮标记：`setAttribute('data-bot-submit','1')`（chaoxing.py 中 L264）
- 新增 `_submit_work_click(frame)`（L694）：用 `frame.locator('[data-bot-submit="1"]').first.click()` 触发 **isTrusted=true 的真实鼠标事件**，坐标来自浏览器计算，从而生成**有效的 `pos`/`rd` 加密值**
- 点击超时 4000ms，失败降级 `force=True` 再试
- 提交前自动处理保存答案、确认弹窗（`version()`）

### 判定标准

| 场景 | `pos` / `rd` | 服务端响应 |
|---|---|---|
| 合成点击（旧，失败） | `pos=10cafdac6d02a5b9da`、`rd=0.7444154504424065` | `{"status":false}` |
| 真实点击（修复后，成功） | `pos=10cafdac6d01bf75d3`、`rd=0.5293247553585375` | `{"msg":"success!","stuStatus":4,...,"status":true}` |

### 回归验证方法（不跑全量，只重放单个课时）

```bash
cd /root/chaoxing_auto
# 杀掉可能残留的进程，重放单个课时（默认 1213004587）
pkill -f replay_work.py; pkill -f 'chromium|chrome'
setsid ./venv/bin/python replay_work.py 1213004587 < /dev/null > replay_check.log 2>&1 &
# 观察进度
tail -f replay_check.log
tail -f work_submit_trace.log   # 确认最终 RESP 是否为 status:true
```

> `setsid ... &` 必须加 `< /dev/null` 并重定向输出，否则 SSH 会话会挂住。

---

## 六、常见问题

**Q1：视频播放过快被平台标记/提示异常？**
把 `finish_mode` 改为 `"watch"`、`video_rate` 改为 `1.0`~`2.0`，慢速真实播放最安全（当前服务器即为 watch + 1.0）。

**Q2：作业提交返回 `{"status":false}`？**
查看 `work_submit_trace.log` 里的 `pos`/`rd`。若值是无效加密值，说明按钮不是通过真实点击触发的。确认用的是修复后的 `chaoxing.py`（L694 `_submit_work_click`），并用上面的重放方法验证。

**Q3：AI 一直答不上来（日志报 `api.deepseek.com` 连接失败 / `Max retries exceeded`）？**
服务器网络无法直连 DeepSeek。检查：
- `config.json` 的 `ai.proxy` 是否指向可用代理（当前 `http://192.168.89.1:7897`）；
- 代理不通时 AI 会自动熔断 5 分钟并走兜底答案（答案可能不准，但作业能提交、任务点能解锁）。

**Q4：章节测验/作业的题没有自动作答？**
学习通页面结构偶尔改版。打开 `config.json` 把 `debug_html` 设为 `true` 重跑一次，脚本会把相关页面存成 `debug_*.html`，把文件发出来定位并更新题型选择器。

**Q5：OCR 装不上 / 报错？**
`ocr.enabled` 改为 `false` 关闭；或先单独执行 `pip install rapidocr-onnxruntime` 查看报错。

**Q6：判断/多选答错？**
AI 默认 `temperature` 较低，通常较准。个别题可把 `ai.model` 换成更强模型，或在 `ai.py` 中调高 `temperature`。

**Q7：登录时没弹出浏览器？**
确保 `headless` 为 `false`，且系统有图形界面（麒麟需登录桌面后再运行）。

**Q8：出现"另一台设备登录"弹窗？**
同一个账号不要同时在多个浏览器/设备登录学习通。若出现，清理 `login_state.json` 让脚本重新登录一次即可：

```bash
rm -f /root/chaoxing_auto/login_state.json
```

---

## 免责声明

1. 使用者**必须遵守所在学校及平台的规章制度**。本脚本仅用于**学习交流使用**。

2. 使用本脚本可能产生的任何后果（包括但不限于：账号异常、课程成绩无效、被平台标记或封禁、违反学校规定等），**作者概不负责**。使用者需自行评估风险，自愿承担全部责任。

3. 本脚本按"现状"提供，作者不对其正确性、可靠性及与平台兼容性作任何明示或默示的保证，亦不承担因使用本脚本而产生的任何直接或间接损失。

## 许可证

本项目采用 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh)（署名-非商业性使用-相同方式共享 4.0 国际）许可协议：

- **署名**：须注明作者 `wjrmnc` 与原仓库地址；
- **非商业**：禁止任何商业用途；
- **相同方式共享**：修改或衍生作品须以相同许可证发布。

详见仓库根目录 [LICENSE](LICENSE)。

> 注：含"禁止商用"条款的许可证不属于 OSI 定义的开源许可证，属于"源码可用（source-available）"。

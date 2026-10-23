#!/bin/bash
# 学习通课程自动学习 - 麒麟/Linux 启动脚本
cd "$(dirname "$0")"

if [ ! -f config.json ]; then
  echo "未找到 config.json，正在从示例复制，请先编辑 config.json 填入账号和 AI key！"
  cp config.example.json config.json
fi

# 自动探测图形界面（让浏览器可见，便于扫码/验证码）
if [ -z "$DISPLAY" ]; then
  for d in :0 :1; do
    if xdpyinfo -display "$d" >/dev/null 2>&1; then
      echo "检测到显示器 $d，使用有头模式"
      export DISPLAY="$d"
      break
    fi
  done
fi

if [ ! -d venv ]; then
  echo "首次运行：创建虚拟环境并安装依赖..."
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip -q
  ./venv/bin/pip install -r requirements.txt
fi

exec ./venv/bin/python main.py

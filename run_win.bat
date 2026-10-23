@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo  学习通课程自动学习 - Windows 启动脚本
echo ============================================
if not exist config.json (
  echo 未找到 config.json，正在从示例复制，请先编辑 config.json 填入账号和 AI key！
  copy config.example.json config.json >nul
)
python -m pip install -r requirements.txt -q
python main.py
pause

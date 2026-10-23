#!/bin/bash
# 用 systemd 严格后台启动学习脚本（与 SSH 会话完全分离），失败自动重启
systemctl stop cxbot 2>/dev/null
systemctl reset-failed cxbot 2>/dev/null
cd /root/chaoxing_auto
systemd-run --unit=cxbot --collect -p Restart=on-failure -p RestartSec=15 bash -c 'cd /root/chaoxing_auto && ./venv/bin/python main.py > /root/chaoxing_run.log 2>&1'
echo "STARTED_VIA_SYSTEMD"
exit 0

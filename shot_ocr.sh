#!/bin/bash
export DISPLAY=:0
kylin-screenshot full > /dev/null 2>&1
sleep 2
F=$(ls -t /root/图片/*.png | head -1)
echo "SHOT=$F"
cd /root/chaoxing_auto
./venv/bin/python run_ocr.py "$F"
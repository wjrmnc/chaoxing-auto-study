#!/bin/bash
echo "---LOG---"
tail -15 /root/chaoxing_run.log
echo "---ACTIVE---"
systemctl is-active cxbot
echo "---CURL---"
for i in 1 2; do
  curl -s -o /dev/null -w "c$i: %{http_code} %{time_total}s\n" -m 8 https://mooc1.chaoxing.com/mycourse/studentcourse
done

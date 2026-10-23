#!/bin/bash
exec >>/root/ipchange.log 2>&1
echo "=== START $(date) ==="
echo "--- before ---"
ip -4 addr show ens33 | grep inet
nmcli con up ens33
echo "UP_RC=$?"
sleep 3
echo "--- after ---"
ip -4 addr show ens33 | grep inet
ip route
echo "=== END ==="

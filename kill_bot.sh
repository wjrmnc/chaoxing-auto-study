#!/bin/bash
PIDS=$(pgrep -f 'venv/bin/python main.py')
if [ -n "$PIDS" ]; then
  echo "killing: $PIDS"
  kill -9 $PIDS
else
  echo "no running bot"
fi
exit 0
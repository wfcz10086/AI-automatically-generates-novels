#!/usr/bin/env bash
# 停长跑。用 pidfile + 进程组，不用 pkill -f（会误伤自己的命令行）。
cd "$(dirname "$0")/.."
if [ -f .longrun.pid ]; then
  PG=$(ps -o pgid= -p "$(cat .longrun.pid)" 2>/dev/null | tr -d " ")
  [ -n "$PG" ] && kill -9 -"$PG" 2>/dev/null
  rm -f .longrun.pid
fi
sleep 1
echo "已停。剩余进程: $(pgrep -f "run[_]until\.sh" | tr "\n" " ")"

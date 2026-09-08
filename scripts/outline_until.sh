#!/usr/bin/env bash
# 排全书细纲的守护 —— 与 run_until.sh 同构。
# cmd_outline 检测到代码变更会退出码 3 交给守护重启；没有守护的话，
# 改一次代码排纲就永久停了（实测排到 79/346 时被我自己的提交打断）。
set -u
cd "$(dirname "$0")/.."
TITLE="${1:?用法: outline_until.sh 书名}"
LOG=reports/outline.log
LOCK=".outline.$(echo -n "$TITLE" | md5sum | cut -c1-8).lock"
PIDFILE=".outline.pid"

exec 9>"$LOCK"
flock -n 9 || { echo "已有排纲在跑（$LOCK）"; exit 1; }
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

mkdir -p reports
echo "=== 排纲守护启动 $(date '+%F %T')  《$TITLE》 ===" | tee -a "$LOG"
while true; do
  python3 run_novel.py outline --title "$TITLE" >> "$LOG" 2>&1
  code=$?
  case $code in
    0) echo "-- 排纲完成 $(date '+%T')" | tee -a "$LOG"; break ;;
    3) echo "-- 代码已更新，热轮转 $(date '+%T')" | tee -a "$LOG"; sleep 2 ;;
    *) echo "-- 异常退出（码 $code），10 秒后重试 $(date '+%T')" | tee -a "$LOG"
       sleep 10 ;;
  esac
done

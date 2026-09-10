#!/usr/bin/env bash
# 自守护哨兵：每 N 秒查一次长跑还在不在，不在就拉起来。
# run_until.sh 管的是「一轮跑崩了续下一轮」，它自己被 kill / OOM / 机器重启就没人管了。
# 这一层管的就是那种情况。
#   bash scripts/guard.sh "书名" [间隔秒=180] [每轮章数=20]
set -uo pipefail
cd "$(dirname "$0")/.."
TITLE="${1:?书名}"; EVERY="${2:-180}"; BATCH="${3:-20}"
SLUG=$(python3 -c "import sys;sys.path.insert(0,'.');from server.orchestrator import slugify;print(slugify('$TITLE'))")
LOG="reports/guard.log"; mkdir -p reports

# 守护自己也要互斥，否则开两个哨兵会各拉一个长跑
exec 8>".guard.$(printf %s "$TITLE" | md5sum | cut -c1-8).lock"
flock -n 8 || { echo "已有哨兵在守《$TITLE》" >&2; exit 1; }

count() { python3 -c "
import json;print(len(json.load(open('projects/$SLUG/state.json',encoding='utf-8')).get('done',[])))" 2>/dev/null || echo 0; }
target() { python3 -c "
import json;print(json.load(open('projects/$SLUG/project.json',encoding='utf-8')).get('target_chapters',0))" 2>/dev/null || echo 0; }

echo "=== 哨兵启动 $(date '+%F %T')  每 ${EVERY}s 查一次《$TITLE》 ===" | tee -a "$LOG"
LAST=$(count); STUCK=0
while :; do
  NOW=$(count); TGT=$(target)
  if [ "$TGT" -gt 0 ] && [ "$NOW" -ge "$TGT" ]; then
    echo "[$(date '+%T')] 已写满 $NOW/$TGT 章，哨兵退出" | tee -a "$LOG"; break
  fi
  # run_until 在跑吗？用它自己的 pid 文件 + 进程名双重判断
  ALIVE=0
  pgrep -f "run_until.sh $TITLE" >/dev/null 2>&1 && ALIVE=1
  pgrep -f "run_novel.py run --title $TITLE" >/dev/null 2>&1 && ALIVE=1
  if [ "$ALIVE" -eq 0 ]; then
    echo "[$(date '+%T')] 长跑不在，拉起（当前 $NOW/$TGT）" | tee -a "$LOG"
    setsid bash scripts/run_until.sh "$TITLE" "$TGT" "$BATCH" >> "$LOG" 2>&1 < /dev/null &
    disown
  else
    # 活着但长时间不涨章 —— 只报警不重启，免得把正在写的那一章打断
    if [ "$NOW" -le "$LAST" ]; then
      STUCK=$((STUCK+1))
      [ $((STUCK % 10)) -eq 0 ] && \
        echo "[$(date '+%T')] 进度停在 $NOW 章已 $((STUCK*EVERY/60)) 分钟（high 档单章本来就慢，仅提示）" | tee -a "$LOG"
    else
      STUCK=0
      echo "[$(date '+%T')] 进度 $NOW/$TGT" | tee -a "$LOG"
    fi
  fi
  LAST=$NOW
  sleep "$EVERY"
done

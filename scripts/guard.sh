#!/usr/bin/env bash
# 自守护哨兵：每 N 秒查一次长跑还在不在，不在就拉起来。
# run_until.sh 管的是「一轮跑崩了续下一轮」，它自己被 kill / OOM / 机器重启就没人管了。
# 这一层管的就是那种情况。
#   bash scripts/guard.sh "书名" [间隔秒=180] [每轮章数=20]
set -uo pipefail
cd "$(dirname "$0")/.."
TITLE="${1:?书名}"; EVERY="${2:-180}"; BATCH="${3:-20}"
#: 停这么多分钟不涨章就判定挂起并重启。单次模型调用有 CALL_BUDGET 兜底,
#: 一章再慢也到不了这个数。
STALL_MIN="${STALL_MIN:-20}"
SLUG=$(python3 -c "import sys;sys.path.insert(0,'.');from server.orchestrator import slugify;print(slugify('$TITLE'))")
LOG="reports/guard.log"; mkdir -p reports

# 守护自己也要互斥，否则开两个哨兵会各拉一个长跑
LOCKF=".guard.$(printf %s "$TITLE" | md5sum | cut -c1-8).lock"
exec 8>"$LOCKF"
if ! flock -n 8; then
  # 报清楚是谁占着 —— 「已有哨兵在守」这一句把「真有哨兵」和「上一个哨兵
  # 死了但它的 sleep 子进程还攥着锁」混成了一种情况, 后者查起来毫无线索。
  HOLD=$(fuser "$LOCKF" 2>/dev/null | tr -d ' ')
  if [ -n "$HOLD" ] && ! ps -o cmd= -p $HOLD 2>/dev/null | grep -q guard.sh; then
    echo "锁被残留进程占着(pid=$HOLD: $(ps -o cmd= -p $HOLD 2>/dev/null))，" \
         "不是哨兵。kill 掉它再重试。" >&2
  else
    echo "已有哨兵在守《$TITLE》(pid=$HOLD)" >&2
  fi
  exit 1
fi

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
    setsid bash scripts/run_until.sh "$TITLE" "$TGT" "$BATCH" >> "$LOG" 2>&1 < /dev/null 8>&- &
    disown
  else
    # 活着但长时间不涨章。原来**只报警不重启**, 理由是「免得打断正在写的
    # 那一章」—— 可实测挂过一次: 模型那边最后一个 chunk 之后再无动静, 连接
    # 一直 ESTABLISHED, 整条流水线干等, 而这一层只会每半小时打一行提示,
    # 没有任何人会来救它。
    # 现在 provider 那边给单次调用钉了总时长上限(CALL_BUDGET), 一章再慢也
    # 不该超过 STALL_MIN 分钟。超了就是真挂了, 该重启。
    if [ "$NOW" -le "$LAST" ]; then
      STUCK=$((STUCK+1))
      MINS=$((STUCK*EVERY/60))
      if [ "$MINS" -ge "$STALL_MIN" ]; then
        echo "[$(date '+%T')] 进度停在 $NOW 章已 $MINS 分钟，判定挂起，重启长跑" | tee -a "$LOG"
        # 用 pidfile + 进程组停, 不用 pkill -f —— 它会误杀哨兵自己的命令行
        # (这个坑踩过三次, 仓库里有测试守着)。run_until 是 setsid 起的,
        # 是进程组长, 杀进程组能连带 run_novel。
        bash scripts/stop_run.sh >> "$LOG" 2>&1
        sleep 3
        if pgrep -f "run[_]until\.sh $TITLE" >/dev/null 2>&1; then
          echo "[$(date '+%T')] ⚠ 停不掉, 下一轮再试" | tee -a "$LOG"
        fi
        STUCK=0
      elif [ $((STUCK % 5)) -eq 0 ]; then
        echo "[$(date '+%T')] 进度停在 $NOW 章已 $MINS 分钟（$STALL_MIN 分钟后重启）" | tee -a "$LOG"
      fi
    else
      STUCK=0
      echo "[$(date '+%T')] 进度 $NOW/$TGT" | tee -a "$LOG"
    fi
  fi
  LAST=$NOW
  # sleep 必须**关掉锁的 fd** 再跑。否则它继承 fd 8, 而 kill 掉哨兵本体时
  # 这个 sleep 会活下来变成孤儿, 攥着 flock 不放 —— 之后每一次重启哨兵都被
  # 拒绝, 而且只留下一句「已有哨兵在守」, 最长堵 EVERY 秒。实测踩过。
  sleep "$EVERY" 8>&-
done

#!/usr/bin/env bash
# 长跑守护：写满目标章节为止，崩了自动续跑（断点在 state.json 里）。
#   bash scripts/run_until.sh "书名" [目标章数] [每轮章数]
set -uo pipefail
cd "$(dirname "$0")/.."
TITLE="${1:?书名}"; TARGET="${2:-0}"; BATCH="${3:-20}"
SLUG=$(python3 -c "import sys;sys.path.insert(0,'.');from server.orchestrator import slugify;print(slugify('$TITLE'))")
LOG="reports/longrun.log"; FAILS=0

# 互斥：同一本书只允许一个长跑。并发两个会互相覆盖 state.json（踩过）。
LOCK=".longrun.$(printf %s "$TITLE" | md5sum | cut -c1-8).lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "已有长跑在写《$TITLE》，本次退出；要重启请先 bash scripts/stop_run.sh" >&2
  exit 1
fi
echo $$ > .longrun.pid
trap 'rm -f .longrun.pid' EXIT

count() { python3 -c "
import json;print(len(json.load(open('projects/$SLUG/state.json',encoding='utf-8')).get('done',[])))" 2>/dev/null || echo 0; }
target() { python3 -c "
import json;print(json.load(open('projects/$SLUG/project.json',encoding='utf-8')).get('target_chapters',0))" 2>/dev/null || echo 0; }
[ "$TARGET" -eq 0 ] && TARGET=$(target)

echo "=== 长跑启动 $(date '+%F %T')  目标 $TARGET 章，当前 $(count) 章 ===" | tee -a "$LOG"
while [ "$(count)" -lt "$TARGET" ]; do
  BEFORE=$(count)
  python3 -u run_novel.py run --title "$TITLE" --chapters "$BATCH" >> "$LOG" 2>&1
  RC=$?
  AFTER=$(count)
  [ "$RC" -eq 3 ] && { echo "-- 代码已更新，热轮转 $(date '+%T')" | tee -a "$LOG"; FAILS=0; continue; }
  if [ "$AFTER" -le "$BEFORE" ]; then
    FAILS=$((FAILS+1))
    echo "!! 本轮无进展（$BEFORE -> $AFTER），第 $FAILS 次；60s 后重试" | tee -a "$LOG"
    [ "$FAILS" -ge 5 ] && { echo "!! 连续 5 轮无进展，停止" | tee -a "$LOG"; exit 1; }
    sleep 60
  else
    FAILS=0
    echo "-- 进度 $AFTER/$TARGET  $(date '+%T')" | tee -a "$LOG"
    # 批间隙消费自愈产生的返修队列（每批最多修 2 章, 不挤占写作时间）
    python3 - "$SLUG" >> "$LOG" 2>&1 <<'PYQ'
import sys, json
sys.path.insert(0, '.')
from server.orchestrator import Project, Novelist
p = Project(sys.argv[1])
q = p._load('repair_queue.json', [])
if q:
    nv = Novelist(p)
    for item in q[:2]:
        try:
            r = nv.rewrite_chapter(item['ch'], mode='polish', note=item['note'])
            print(f"[repair] 第{item['ch']}章 -> {r.get('score')}")
        except Exception as e:
            print(f"[repair] 第{item['ch']}章失败: {e}")
    p.write('repair_queue.json', json.dumps(q[2:], ensure_ascii=False, indent=2))
PYQ
  fi
done
echo "=== 写完 $(count) 章 $(date '+%F %T') ===" | tee -a "$LOG"
python3 run_novel.py status --title "$TITLE" | tee -a "$LOG"

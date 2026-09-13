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
  # 4 = 书级熔断: 连片需人工, 同一个根子在批量产废品。不是失败, 不重试 ——
  # 重试只会接着烧。人处理完删掉 HALT.md, 守护那层会重新拉起。
  [ "$RC" -eq 4 ] && { echo "!! 书级熔断, 长跑退出等人 $(date '+%T')" | tee -a "$LOG"; exit 0; }
  # 5 = 达到总字数上限, 完本 —— 不是故障
  [ "$RC" -eq 5 ] && { echo "== 达到总字数上限, 完本 $(date '+%T')" | tee -a "$LOG"; exit 0; }
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
    rest = list(q[2:])
    for item in q[:2]:
        # 修完要复审才知道该不该出队。原来无条件 q[2:] 丢掉前两条 ——
        # 没修好的也当修好了; 而 audit 那边状态又不更新, 于是「修好了还一直
        # 报警」和「没修好却被丢出队列」两种错同时存在。
        tries = int(item.get('tries') or 0) + 1
        try:
            # 冲突/时间线这类**结构病**, polish(只改语言)治不了 —— 实测 6 章
            # 「重写之后仍然该拦」全是这一类。升 replace: 剧情可调, 衔接前后。
            _mode = 'replace' if any(w in str(item.get('note') or '')
                                     for w in ('冲突', '时间线', '矛盾', '跳变')) \
                    else 'polish'
            r = nv.rewrite_chapter(item['ch'], mode=_mode, note=item['note'])
            st = r.get('status') or '?'
            print(f"[repair] 第{item['ch']}章 -> {r.get('score')} / {st}"
                  f" ({r.get('issues') or '无问题'})")
            if st in ('需人工', '失败') and tries < 2:
                item['tries'] = tries
                rest.append(item)          # 还没好, 留着下一批再修一次
            elif st in ('需人工', '失败'):
                print(f"[repair] 第{item['ch']}章修了 {tries} 次仍未过，"
                      f"留给人工，不再自动重试")
        except Exception as e:
            print(f"[repair] 第{item['ch']}章失败: {e}")
            if tries < 2:
                item['tries'] = tries
                rest.append(item)
    p.write('repair_queue.json', json.dumps(rest, ensure_ascii=False, indent=2))
PYQ
  fi
done
echo "=== 写完 $(count) 章 $(date '+%F %T') ===" | tee -a "$LOG"
python3 run_novel.py status --title "$TITLE" | tee -a "$LOG"

#!/usr/bin/env bash
# 排队：等上一本写完，自动开下一本。避免两本并发抢模型。
set -uo pipefail
cd "$(dirname "$0")/.."
WAIT_BOOK="${1:?等待哪本}"; NEXT_BOOK="${2:?接着写哪本}"; TARGET="${3:-192}"
cnt() { python3 -c "
import json;print(len(json.load(open('projects/$1/state.json',encoding='utf-8')).get('done',[])))" 2>/dev/null || echo 0; }
tgt() { python3 -c "
import json;print(json.load(open('projects/$1/project.json',encoding='utf-8')).get('target_chapters',0))" 2>/dev/null || echo 0; }
echo "[queue] 等《$WAIT_BOOK》写完（$(cnt "$WAIT_BOOK")/$(tgt "$WAIT_BOOK")）…"
while [ "$(cnt "$WAIT_BOOK")" -lt "$(tgt "$WAIT_BOOK")" ]; do
  pgrep -f 'run[_]until\.sh' >/dev/null || { echo "[queue] 上一本的守护已退出，提前接管"; break; }
  sleep 60
done
echo "[queue] 《$WAIT_BOOK》完成 $(cnt "$WAIT_BOOK") 章，开始写《$NEXT_BOOK》"
exec bash scripts/run_until.sh "$NEXT_BOOK" "$TARGET" 20

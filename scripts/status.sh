#!/usr/bin/env bash
# 全量播报: 守护在做什么、模型请求、提炼动作、质量数字。
cd "$(dirname "$0")/.."
T="${1:-金钟镇天下}"
echo "════════ $(date +%H:%M:%S)  《$T》 ════════"
echo "【进程】"
ps -eo pid,etimes,args | grep -E "[r]un_until|[r]un_novel|[g]uard\.sh" | grep "$T" \
  | awk '{printf "  %-8s 已跑%6ss  %s\n",$1,$2,$3" "$4}' || echo "  ✗ 没有进程在跑"
echo "【哨兵】每 180s 一查，掉线即拉起"
tail -2 /tmp/claude-1000/-opt-AI-automatically-generates-novels/*/scratchpad/guard*.log 2>/dev/null | grep -E "进度|拉起|启动" | tail -2 | sed 's/^/  /'
echo "【写手最近动作】"
grep -vE "search:bocha" reports/longrun.log | tail -5 | cut -c1-116 | sed 's/^/  /'
echo "【模型请求】"
python3 - "$T" <<'PY'
import json,glob,os,sys,time
from collections import Counter
from pathlib import Path
import re
slug=re.sub(r"[^\w一-鿿-]+","_",sys.argv[1]).strip("_")[:60]
fs=sorted(glob.glob(f'projects/{slug}/trace/*.json'),key=os.path.getmtime)
c=Counter(); last=None
for f in fs[-600:]:
    try: d=json.load(open(f,encoding='utf-8'))
    except: continue
    c[d.get('profile') or '?']+=1
    last=(time.strftime('%H:%M:%S',time.localtime(os.path.getmtime(f))),d.get('profile'),d.get('model'))
print('  ',dict(c),'| 共',sum(c.values()),'次(trace 只留最近若干)')
if last: print(f'   最近: {last[0]} {last[1]} / {last[2]}')
PY
echo "【提炼/截断】"
p=$(grep -c "\[提炼\]" reports/longrun.log 2>/dev/null; true)
q=$(grep -c "\[clip\]" reports/longrun.log 2>/dev/null || echo 0)
echo "  提炼 $p 次 | 带记账截断 $q 次"
grep -E "\[提炼\]|\[clip\]" reports/longrun.log 2>/dev/null | tail -3 | sed 's/^/  /'
echo "【质量】"
python3 - "$T" <<'PY'
import json,glob,re,sys,statistics
slug=re.sub(r"[^\w一-鿿-]+","_",sys.argv[1]).strip("_")[:60]
sc=[json.load(open(f,encoding='utf-8')).get('overall') for f in sorted(glob.glob(f'projects/{slug}/audit/*.critique.json'))]
sc=[x for x in sc if x]
ws=[len(re.findall(r'[一-鿿]',open(f,encoding='utf-8').read())) for f in sorted(glob.glob(f'projects/{slug}/chapters/*.md'))]
if sc: print(f"  评审 {sc} | 均 {statistics.mean(sc):.1f}")
if ws: print(f"  字数 {ws} | 区间内 {sum(1 for w in ws if 2400<=w<=3220)}/{len(ws)} | 累计 {sum(ws):,} 字")
PY

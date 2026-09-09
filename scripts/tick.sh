#!/bin/bash
# 一分钟哨兵：报一次当前进度然后退出，退出通知把我叫醒。
P="projects/${1:-大宋奸商西门庆}"
sleep "${2:-60}"
python3 - "$P" <<'PY'
import json, sys, pathlib, os, time
d = pathlib.Path(sys.argv[1])
def j(f, dv):
    p = d/f
    return json.load(open(p, encoding='utf-8')) if p.exists() else dv
co = j('chapter_outlines.json', {})
ok = sorted(int(k) for k, v in co.items() if len(str(v)) > 200)
st = j('state.json', {})
tgt = j('project.json', {}).get('meta', {}).get('target_chapters', '?')
tr = list((d/'trace').glob('*.json'))
built = [f for f in ('world_bible.md','characters.md','roster.json','chapter_outlines.json',
                     'stages.json','threads.json','ladders.json') if (d/f).exists()]
print(f"[{time.strftime('%H:%M:%S')}] 细纲 {len(ok)}/{tgt}｜最大 {ok[-1] if ok else 0}｜"
      f"空洞 {[n for n in range(1, (ok[-1] if ok else 0)+1) if n not in ok][:8]}｜调用 {len(tr)}")
print("  已建:", " ".join(built))
if tr:
    last = max(tr, key=lambda f: f.stat().st_mtime)
    print(f"  最近调用 {last.name} @ {time.strftime('%H:%M:%S', time.localtime(last.stat().st_mtime))}")
PY
pgrep -f outline_until >/dev/null && echo "  守护 在" || echo "  !! 守护 掉了"

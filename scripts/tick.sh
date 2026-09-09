#!/bin/bash
# 一分钟哨兵：报进度 + 这一分钟花了多少 token、调了什么、查了什么，然后退出。
P="projects/${1:-大宋奸商西门庆}"
W="${2:-60}"
python3 - "$P" "$W" <<'PY'
import json, sys, pathlib, time, collections
d, win = pathlib.Path(sys.argv[1]), int(sys.argv[2])
t0 = time.time()
time.sleep(win)

def j(f, dv):
    p = d/f
    try: return json.load(open(p, encoding='utf-8')) if p.exists() else dv
    except Exception: return dv

co = j('chapter_outlines.json', {})
ok = sorted(int(k) for k, v in co.items() if len(str(v)) > 200)
pj = j('project.json', {})
tgt = pj.get('target_chapters') or (pj.get('meta') or {}).get('target_chapters') or '?'
hole = [n for n in range(1, (ok[-1] if ok else 0)+1) if n not in ok]
STAGE = [('world_bible.md','世界观'), ('characters.md','角色档案'), ('roster.json','花名册'),
         ('outline.md','总纲'), ('volumes.json','分卷'), ('chapter_outlines.json','细纲')]
nxt = next((n for f, n in STAGE if not (d/f).exists()), '细纲')

tr = sorted((d/'trace').glob('*.json'), key=lambda f: f.stat().st_mtime)
fresh = [f for f in tr if f.stat().st_mtime >= t0]
by = collections.defaultdict(lambda: [0, 0, 0, 0.0])   # 次数/入/出/秒
for f in fresh:
    try: r = json.load(open(f, encoding='utf-8'))
    except Exception: continue
    u, s = r.get('usage') or {}, by[r.get('profile', '?')]
    s[0] += 1; s[1] += u.get('prompt', 0); s[2] += u.get('completion', 0)
    s[3] += r.get('elapsed') or 0

print(f"[{time.strftime('%H:%M:%S')}] 细纲 {len(ok)}/{tgt}｜最大 {ok[-1] if ok else 0}"
      f"｜空洞 {hole[:8] or '无'}｜在做「{nxt}」")
if by:
    tin = sum(v[1] for v in by.values()); tout = sum(v[2] for v in by.values())
    print(f"  本分钟 {len(fresh)} 次调用｜入 {tin:,} tok / 出 {tout:,} tok"
          f"｜合计 {tin+tout:,}")
    for k, (n, i, o, s) in sorted(by.items(), key=lambda x: -x[1][2]):
        print(f"    {k:<10}{n:>3} 次  入 {i:>7,}  出 {o:>7,}  {s:>6.1f}s")
else:
    print("  本分钟无调用（可能卡在一次长生成里）")

log = pathlib.Path('.cache/search_log.jsonl')
if log.exists():
    qs = []
    for ln in log.read_text(encoding='utf-8').splitlines()[-200:]:
        try: r = json.loads(ln)
        except Exception: continue
        if time.mktime(time.strptime(r['at'], '%Y-%m-%d %H:%M:%S')) >= t0:
            qs.append(r)
    if qs:
        miss = sum(1 for r in qs if not r['hit'])
        print(f"  检索 {len(qs)} 次（真花钱 {miss}，缓存命中 {len(qs)-miss}）：")
        for r in qs[-6:]:
            tag = '缓存' if r['hit'] else ('失败' if r['n'] < 0 else f"{r['n']}条")
            print(f"    [{tag}] {r['q'][:52]}")
PY
pgrep -f 'outline_unti[l]' >/dev/null && echo "  守护 在" || echo "  !! 守护 掉了"

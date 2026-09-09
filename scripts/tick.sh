#!/bin/bash
# 一分钟哨兵：报进度 + 本窗口花了多少 token / 多快 / 调了什么 / 查了什么 / 最新产出。
P="projects/${1:-大宋奸商西门庆}"
W="${2:-60}"
python3 - "$P" "$W" <<'INNER'
import json, sys, pathlib, time, collections
d, win = pathlib.Path(sys.argv[1]), int(sys.argv[2])
t0 = time.time()
time.sleep(win)

def j(f, dv):
    try: return json.load(open(d/f, encoding='utf-8')) if (d/f).exists() else dv
    except Exception: return dv

co = j('chapter_outlines.json', {})
ok = sorted(int(k) for k, v in co.items() if len(str(v)) > 200)
pj = j('project.json', {})
tgt = pj.get('target_chapters') or (pj.get('meta') or {}).get('target_chapters') or '?'
hole = [n for n in range(1, (ok[-1] if ok else 0)+1) if n not in ok]
STAGE = [('world_bible.md','世界观'), ('characters.md','角色档案'), ('roster.json','花名册'),
         ('outline.md','总纲'), ('volumes.json','分卷'), ('chapter_outlines.json','细纲')]
nxt = next((n for f, n in STAGE if not (d/f).exists()), '细纲')
print(f"[{time.strftime('%H:%M:%S')}] 细纲 {len(ok)}/{tgt}｜最大 {ok[-1] if ok else 0}"
      f"｜空洞 {hole[:8] or '无'}｜在做「{nxt}」")

# 有的网关不回 usage(qwen 这条线就不回)。回退到按字数折算 —— 实测本项目
# 387 条有 usage 的样本, 中文「出字 / 出 token」= 0.85。估的比没有强, 标出来。
RATIO = 0.85
by = collections.defaultdict(lambda: [0, 0, 0, 0.0, False])   # 次数/入/出/秒/估
fresh = [f for f in (d/'trace').glob('*.json') if f.stat().st_mtime >= t0]
for f in fresh:
    try: r = json.load(open(f, encoding='utf-8'))
    except Exception: continue
    u = r.get('usage') or {}
    s = by[r.get('profile', '?')]
    s[0] += 1
    s[1] += u.get('prompt') or int((r.get('prompt_chars') or 0) / RATIO)
    s[2] += u.get('completion') or int(((r.get('out_chars') or 0)
                                        + (r.get('reasoning_chars') or 0)) / RATIO)
    s[3] += r.get('elapsed') or 0
    s[4] = s[4] or not u

if by:
    tin = sum(v[1] for v in by.values()); tout = sum(v[2] for v in by.values())
    tsec = sum(v[3] for v in by.values())
    print(f"  {len(fresh)} 次调用｜入 {tin:,} / 出 {tout:,} tok｜合计 {tin+tout:,}"
          f"｜{tout/tsec if tsec else 0:.1f} tok/s")
    for k, (n, i, o, s, e) in sorted(by.items(), key=lambda x: -x[1][2]):
        print(f"    {k:<10}{n:>3} 次  入 {i:>7,}  出 {o:>7,}  {s:>6.1f}s  "
              f"{o/s if s else 0:>5.1f} tok/s{'  (估)' if e else ''}")
else:
    print("  本分钟无调用（可能卡在一次长生成里）")

log = pathlib.Path('.cache/search_log.jsonl')
if log.exists():
    qs = []
    for ln in log.read_text(encoding='utf-8').splitlines()[-300:]:
        try: r = json.loads(ln)
        except Exception: continue
        if time.mktime(time.strptime(r['at'], '%Y-%m-%d %H:%M:%S')) >= t0: qs.append(r)
    if qs:
        miss = sum(1 for r in qs if not r['hit'])
        print(f"  检索 {len(qs)} 次（真花钱 {miss}，缓存命中 {len(qs)-miss}）：")
        for r in qs[-5:]:
            tag = '缓存' if r['hit'] else ('失败' if r['n'] < 0 else f"{r['n']}条")
            print(f"    [{tag}] {r['q'][:50]}")

# 光看 token 数看不出写得好不好, 得看见字。
best, tsp = None, 0
for f in list(d.glob('*.md')) + list(d.glob('*.json')):
    if f.name in ('project.json', 'state.json', 'facts.json'): continue
    if f.stat().st_mtime > tsp: best, tsp = f, f.stat().st_mtime
if best and tsp >= t0 - 600:
    body = best.read_text(encoding='utf-8', errors='replace')
    print(f"  ── 最新产出 {best.name}｜{len(body):,} 字｜"
          f"{time.strftime('%H:%M:%S', time.localtime(tsp))}")
    shown = 0
    for ln in body.splitlines():
        s = ln.strip()
        if len(s) > 30 and not s.startswith('#'):
            print("     " + s[:170]); shown += 1
            if shown >= 2: break
INNER
pgrep -f 'outline_unti[l]' >/dev/null && echo "  守护 在" || echo "  !! 守护 掉了"

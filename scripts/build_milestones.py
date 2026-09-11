#!/usr/bin/env python3
"""根合同 → 30 节里程碑链。三个候选并发生成, 程序打分选优。

用法: python3 scripts/build_milestones.py --tree <目录> [--k 30] [--n 3]
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import tree as tr                                # noqa: E402
from server.orchestrator import call                         # noqa: E402


def say(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def one_candidate(tag: str, root, k: int):
    t = time.time()
    try:
        r = call("planning", tr.p_milestones(root, k), max_tokens=16000)
        ms = tr.parse_milestones((r.text or ""), root)
        errs = tr.check_milestones(root, ms) if ms else ["没解析出来"]
        # 违约打回去修一轮(只修不重来)
        if ms and errs:
            r2 = call("planning", tr.p_repair(root, errs, r.text), max_tokens=16000)
            ms2 = tr.parse_milestones((r2.text or ""), root)
            if ms2:
                e2 = tr.check_milestones(root, ms2)
                if len(e2) < len(errs):
                    ms, errs = ms2, e2
        sc = tr.score_milestones(root, ms)
        say(f"  候选{tag}: {len(ms)}节 违约{len(errs)} 得分{sc:.1f} "
            f"{time.time()-t:.0f}s")
        return tag, ms, errs, sc
    except Exception as e:
        say(f"  候选{tag}: 失败 {type(e).__name__}: {str(e)[:60]}")
        return tag, [], [str(e)], -1e9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--k", type=int, default=30)
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    d = Path(a.tree)
    nodes = {k: tr.Node.from_dict(v) for k, v in
             json.loads((d / "tree.json").read_text(encoding="utf-8")).items()}
    root = nodes["R"]
    say(f"《{root.title}》根合同 → {a.k} 节里程碑 × {a.n} 候选(温度已拉满, 各自会长得不一样)")
    with cf.ThreadPoolExecutor(max_workers=a.n) as ex:
        cands = list(ex.map(lambda t: one_candidate(t, root, a.k),
                            [chr(65 + i) for i in range(a.n)]))
    cands.sort(key=lambda x: -x[3])
    tag, ms, errs, sc = cands[0]
    if not ms:
        say("三个候选全失败"); return 1
    say(f"选中候选{tag}（得分 {sc:.1f}, 残余违约 {len(errs)}）")
    for e in errs[:6]:
        say("   ⚠ " + e)
    root.children = [m.id for m in ms]
    nodes = {"R": root, **{m.id: m for m in ms}}
    (d / "tree.json").write_text(json.dumps({k: v.to_dict() for k, v in nodes.items()},
                                            ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    for m in ms[:8]:
        say(f"  {m.id:5s} {m.start:>3}-{m.end:<3} {m.title[:14]} | 解决:{m.solves[:20]} | 但是:{m.exposes[:20]}")
    say(f"...共 {len(ms)} 节, 已写入 {d/'tree.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""阶段骨架体检 —— 在动笔之前，先看细纲的结构有没有塌。

把总纲读成结构化的阶段骨架（目标→步骤→功能位→人）、张力账与承诺清单，
然后拿已排好的细纲去核对：

  · 功能位空缺        这一阶段没有阻挡者/没有人付代价 = 没有戏
  · 未登记角色        骨架点了名、花名册里没有 → 排纲白名单会把他挡掉
  · 弧光停滞          连续多个阶段功能位没变过的人，读起来像工具
  · 张力静默消解      压着的账，一方悄悄消失了
  · 承诺挨饿          总纲承诺的成长线/道具/关系，久未推进

产出写进 stages.json / state.json，排纲时作为约束注入。

    python3 scripts/stagecheck.py --title 书名 [--rebuild] [--dry]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import stagecraft as sc                          # noqa: E402
from server.orchestrator import Novelist, Project, call, clean  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--rebuild", action="store_true", help="重建骨架，忽略已有缓存")
    ap.add_argument("--dry", action="store_true", help="只报告，不落盘")
    a = ap.parse_args()

    nv = Novelist(Project(a.title))
    outlines = nv.p._load("chapter_outlines.json", {})
    if not outlines:
        print("还没有细纲，先跑 outline")
        return 1
    upto = max(int(k) for k in outlines)
    roster = [c["name"] for c in nv.roster()]
    total = int(nv.p.meta.get("target_chapters") or upto)
    print(f"《{a.title}》细纲 {len(outlines)} 章（至第 {upto} 章），花名册 {len(roster)} 人\n")

    def ask(prompt: str) -> str:
        return clean(call("planning", prompt, max_tokens=4000).text)

    # ---------- 骨架 / 张力 / 承诺 ----------
    st = nv.p.state
    stages = nv.p._load("stages.json", []) if not a.rebuild else []
    if not stages:
        print("读总纲 → 阶段骨架…")
        stages = sc.build_stages(outline=nv.asset("outline.md"), total_chapters=total,
                                 title=a.title, genre=nv.genre, roster=roster, ask=ask)
        if stages and not a.dry:
            nv.p.write("stages.json", json.dumps(stages, ensure_ascii=False, indent=2))
    tensions = st.get("tensions") or []
    if not tensions or a.rebuild:
        print("读总纲+人物档案 → 张力账…")
        tensions = sc.build_tensions(outline=nv.asset("outline.md"),
                                     characters=nv.p.read("characters.md"),
                                     title=a.title, ask=ask)
    promises = st.get("promises") or []
    if not promises or a.rebuild:
        print("读总纲 → 承诺清单…")
        promises = sc.build_promises(outline=nv.asset("outline.md"),
                                     title=a.title, ask=ask)

    if not stages:
        print("骨架没建起来（模型没给出可解析的 JSON），先看日志")
        return 2

    # 主角在档案里叫「西门庆（林远）」，出场角色栏里写「西门庆」——别名不合一，
    # 主角会被判定 346 章没出过场，所有张力全部误报「静默消解」。
    aliases = {}
    pair = nv.alias_pair() or []
    if len(pair) >= 2:
        aliases = {sc.canon_name(pair[1]): sc.canon_name(pair[0]),
                   pair[1]: sc.canon_name(pair[0])}
    app = sc.cast_appearances(outlines, aliases=aliases)

    # 承诺的「末次推进」：关键词只做零成本预筛，选出候选章
    for p in promises:
        kws = [p["text"][:6]] + list(p.get("keywords") or [])
        hits = [n for n in sorted(int(k) for k in outlines)
                if any(k and k in outlines[str(n)] for k in kws)]
        p["last_advanced"] = hits[-1] if hits else 0
        p["_hits"] = len(hits)

    # ---------- 报告 ----------
    print(f"\n{'='*66}\n阶段骨架（{len(stages)} 段）\n{'='*66}")
    for s in stages:
        lab = s.get("labels") or {}
        print(f"\n■ {s['name']}  第{s['start']}-{s['end']}章")
        print(f"  目标：{s['goal']}")
        if s.get("steps"):
            print(f"  步骤：{' → '.join(s['steps'])}")
        for k in sc.SLOT_KEYS:
            who = (s.get("roles") or {}).get(k) or []
            mark = "" if who else ("   ← 空缺" if k in sc.REQUIRED_SLOTS else "")
            print(f"    {lab.get(k, k):<6}{'、'.join(who) or '—':<24}{mark}")
        if s.get("exit"):
            print(f"  出口状态：{s['exit']}")

    issues = 0
    print(f"\n{'='*66}\n体检结论\n{'='*66}")

    for s in stages:
        vac = sc.vacancies(s)
        if vac:
            issues += 1
            print(f"⚠ 功能位空缺  {s['name']}：{'、'.join(vac)} 无人 —— 这一阶段少了对应的戏")

    unreg = sc.unregistered(stages, roster, aliases)
    if unreg:
        issues += 1
        print(f"\n⚠ 未登记角色 {len(unreg)} 人（骨架点了名，花名册没有 → 排纲白名单会挡掉）")
        for nm, where in unreg[:15]:
            cs = [n for n in sorted(int(k) for k in outlines) if nm in outlines[str(n)]]
            cast_n = len(app.get(nm) or [])
            print(f"    {nm:<8} 占位 {where:<22} 细纲提及 {len(cs):>3} 次 / 出场 {cast_n:>3} 章")

    frozen = sc.arc_frozen(stages, aliases=aliases)
    if frozen:
        issues += 1
        print(f"\n⚠ 弧光停滞")
        for f in frozen:
            print(f"    {f}")

    silent = sc.silent_resolution(tensions, app, upto, aliases=aliases)
    if silent:
        issues += 1
        print(f"\n⚠ 张力被静默消解")
        for x in silent:
            print(f"    {x}")
    if tensions:
        print(f"\n  张力账（{len(tensions)} 条）")
        for t in tensions:
            last = {sc.canon_name(nm, aliases): (app.get(sc.canon_name(nm, aliases)) or [0])[-1]
                    for nm in t["between"]}
            print(f"    {' ↔ '.join(t['between']):<20}{t['state']:<6}{t['about'][:34]}"
                  f"   末次出场 {last}")

    hungry = sc.starving(promises, upto)
    if hungry:
        issues += 1
        print(f"\n⚠ 承诺挨饿")
        for h in hungry:
            print(f"    {h}")
    if promises:
        print(f"\n  承诺清单（{len(promises)} 条，末次提及章）")
        for p in promises:
            print(f"    [{p.get('kind',''):<4}] {p['text'][:44]:<46}"
                  f"命中 {p['_hits']:>3} 章，末次第 {p['last_advanced']} 章")

    print(f"\n共 {issues} 类结构问题")
    if not a.dry:
        for p in promises:
            p.pop("_hits", None)
        st["tensions"], st["promises"] = tensions, promises
        nv.p.state = st
        nv.p.save()
        print("已落盘：stages.json / state.tensions / state.promises")
    return 0


if __name__ == "__main__":
    sys.exit(main())

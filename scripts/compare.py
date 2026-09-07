#!/usr/bin/env python3
"""两稿质量对比 —— 用同一把尺子量新旧机制的产出。

  python3 scripts/compare.py 项目A 项目B
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.evaluator import book_audit, REAL_PEOPLE, CLICHE_PATTERNS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "projects"


def _book(slug):
    """角色名与禁用词从项目自己取 —— 写死某本书的人名和朝代，
    换一本书这个工具就报的是别人的账。"""
    from server.orchestrator import Novelist, Project
    try:
        nv = Novelist(Project(slug))
        names = [c["name"] for c in nv.roster()]
        anchor = nv.world_anchor()
        forb = [w for w in (anchor.get("forbidden") or []) if w not in set(names)]
        return names, forb, anchor.get("forbidden_people")
    except Exception:
        return [], [], REAL_PEOPLE


def measure(slug: str):
    d = ROOT / slug
    ch = {int(p.stem): p.read_text(encoding="utf-8")
          for p in sorted((d / "chapters").glob("[0-9]*.md"))}
    if not ch:
        return None
    names, forb, people = _book(slug)
    r = book_audit(ch, characters=names, forbidden_terms=forb,
                   forbidden_people=people,
                   protagonist=(names[0] if names else ""))
    lens = [len(re.findall(r"[一-鿿]", v)) for v in ch.values()]
    scores = []
    for n in ch:
        a = d / f"audit/{n:03d}.json"
        if a.exists():
            try:
                scores.append(json.loads(a.read_text(encoding="utf-8"))["score"])
            except Exception:
                pass
    allt = "\n".join(ch.values())
    tics = {name: len(re.findall(pat, allt)) for pat, name in CLICHE_PATTERNS}
    return {
        "slug": slug, "chapters": len(ch), "words": r["total_words"],
        "book_score": r["score"],
        "chapter_score_avg": round(sum(scores) / len(scores), 1) if scores else None,
        "avg_len": round(sum(lens) / len(lens)),
        "len_range": f"{min(lens)}-{max(lens)}",
        "issues": {i["type"]: i["level"] for i in r["issues"]},
        "tics": {k: v for k, v in sorted(tics.items(), key=lambda x: -x[1]) if v},
        "forbidden": {w: allt.count(w) for w in forb if w in allt},
        "real_people": {w: allt.count(w) for w in REAL_PEOPLE if w in allt},
        "cast": {c["name"]: c["count"] for c in r["cast"][:8]},
    }


def per10k(n, words):
    return round(n / max(1, words / 10000), 1)


if __name__ == "__main__":
    a, b = sys.argv[1], sys.argv[2]
    A, B = measure(a), measure(b)
    if not A or not B:
        sys.exit("有一侧没有章节")

    def row(label, x, y, better="low"):
        mark = ""
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if x != y:
                good = (y > x) if better == "high" else (y < x)
                mark = " ✅" if good else " ⚠️"
        return f"| {label} | {x} | {y} |{mark}"

    print(f"# 质量对比\n\n| 指标 | {a} | {b} | |\n|---|---|---|---|")
    print(row("章节数", A["chapters"], B["chapters"], "high"))
    print(row("总字数", A["words"], B["words"], "high"))
    print(row("**全书体检**", A["book_score"], B["book_score"], "high"))
    print(row("逐章均分", A["chapter_score_avg"], B["chapter_score_avg"], "high"))
    print(row("单章字数均值", A["avg_len"], B["avg_len"]))
    print(f"| 字数区间 | {A['len_range']} | {B['len_range']} | |")
    print(row("禁用术语总次数", sum(A["forbidden"].values()), sum(B["forbidden"].values())))
    print(row("真实历史人物次数", sum(A["real_people"].values()), sum(B["real_people"].values())))
    print(row("口癖总次数/万字",
              per10k(sum(A["tics"].values()), A["words"]),
              per10k(sum(B["tics"].values()), B["words"])))
    print(row("问题条数", len(A["issues"]), len(B["issues"])))

    print(f"\n## 口癖 Top（每万字）\n\n| 口癖 | {a} | {b} |\n|---|---|---|")
    keys = list(dict.fromkeys(list(A["tics"])[:8] + list(B["tics"])[:8]))
    for k in keys:
        print(f"| {k} | {per10k(A['tics'].get(k,0),A['words'])} | "
              f"{per10k(B['tics'].get(k,0),B['words'])} |")

    print(f"\n## 问题清单\n\n**{a}**: " +
          ("、".join(f"{k}({v})" for k, v in A["issues"].items()) or "无"))
    print(f"\n**{b}**: " +
          ("、".join(f"{k}({v})" for k, v in B["issues"].items()) or "无"))

    print(f"\n## 角色戏份分布\n\n| 角色 | {a} | {b} |\n|---|---|---|")
    for k in list(dict.fromkeys(list(A["cast"])[:6] + list(B["cast"])[:6])):
        print(f"| {k} | {A['cast'].get(k,0)} | {B['cast'].get(k,0)} |")

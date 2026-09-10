#!/usr/bin/env python3
"""写完一章之后，把台账推到位 —— 这是「文件当记忆」的写回端。

不更新台账等于没写：下一章读到的还是旧状态，错会一直往后滚。实测子弹账
从第 104 章的 105 发涨回第 145 章的 119 发，中间 40 章没人提，一路烂到
第 249 章才被抓出来。

**用正文而不是细纲回填**。细纲只是计划，正文才是真发生的事：一条支线在
细纲里写了、正文里没写到，那它就没露面，不该记成已推进。

    python3 scripts/after_chapter.py --title 书名 --chapter 12
    python3 scripts/after_chapter.py --title 书名 --chapter 12 --summary "自定义摘要"
    python3 scripts/after_chapter.py --title 书名 --chapter 12 --dry
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import stagecraft as sc                     # noqa: E402
from server.orchestrator import Novelist, Project       # noqa: E402


def one_liner(nv: Novelist, n: int) -> str:
    """摘要默认取细纲的「一句话」—— 它本来就是这一章的一句话概括。"""
    co = nv.p._load("chapter_outlines.json", {})
    m = re.search(r"一句话[:：]\s*(.+)", str(co.get(str(n), "")))
    return m.group(1).strip() if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--summary", default="", help="不给就取细纲的「一句话」")
    ap.add_argument("--dry", action="store_true", help="只报，不写")
    a = ap.parse_args()

    nv = Novelist(Project(a.title))
    n = a.chapter
    body = nv.p.chapter(n)
    if not body:
        print(f"✗ 第{n}章正文不存在（projects/{a.title}/chapters/）")
        return 1

    st = nv.p.state
    words = len(re.sub(r"\s", "", body))

    # ① 摘要 + 进度
    summ = a.summary or one_liner(nv, n) or body[:60]
    st.setdefault("summaries", {})[str(n)] = summ
    done = st.setdefault("done", [])
    if n not in done:
        done.append(n)
        done.sort()
    st["current"] = max(int(st.get("current") or 0), n)

    # ② 支线末次露面 / 承诺末次推进 —— 拿**已写出的正文**回填，不是细纲
    bodies = {}
    for i in sorted(done):
        t = nv.p.chapter(i)
        if t:
            bodies[str(i)] = t
    try:
        thr = nv.threads()
        sc.thread_last_seen(thr, bodies, nv.name_aliases(),
                            protagonist=(nv.alias_pair() or [""])[0])
        if not a.dry:
            # Project 没有 _save，写盘走 write(名, 文本)。第一版写成 p._save(...)，
            # 异常被下面的 except 吞掉 —— threads.json 永远存不下去还不报错。
            nv.p.write("threads.json", json.dumps(thr, ensure_ascii=False, indent=2))
    except Exception as e:                    # 台账坏了不该挡住写作
        thr = []
        print(f"  支线回填跳过: {e}")
    try:
        sc.promise_last_seen(st.get("promises") or [], bodies)
    except Exception as e:
        print(f"  承诺回填跳过: {e}")

    if not a.dry:
        nv.p.save()

    tgt = nv.target_words() if hasattr(nv, "target_words") else 0
    flag = "" if not tgt else ("  ⚠ 字数不足" if words < tgt * 0.9 else "")
    print(f"第{n}章 {words}字{flag}｜已写 {len(done)} 章")
    print(f"  摘要：{summ[:70]}")

    # ③ 台账硬矛盾 —— 每章都查，别等 25 章的自审
    try:
        for x in nv.outline_finite_check(name_chapters=True):
            print(f"  ⚠ {x[:160]}")
    except Exception as e:
        print(f"  台账核对跳过: {e}")

    # ④ 饿太久的支线：这一章之后谁该露面了
    starving = [t for t in thr
                if n - int(t.get("last_touched") or (t.get("span") or [0])[0])
                >= int(t.get("cadence") or 12)
                and n <= int((t.get("span") or [0, 10 ** 9])[1])]
    for t in starving[:4]:
        gap = n - int(t.get("last_touched") or (t.get("span") or [0])[0])
        print(f"  ⚠ 支线「{t.get('name')}」已 {gap} 章没露面"
              f"（每 {t.get('cadence')} 章至少一次），承载者：{t.get('owner') or t.get('org') or '—'}")

    if a.dry:
        print("  （--dry：未写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

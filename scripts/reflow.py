#!/usr/bin/env python3
"""把已写章节重新泡一遍：清元数据 + 扩写到文风包规格 + 重新评分。

用途：改了字数闸门或清洗规则之后，前面已经写坏的章节不该留着。
重写会把写好的内容弄丢，所以这里只做「扩写」——不改情节、不加人物。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.evaluator import audit                      # noqa: E402
from server.orchestrator import Novelist, Project, call, clean  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--from", dest="a", type=int, default=1)
    ap.add_argument("--to", dest="b", type=int, default=0)
    ap.add_argument("--dry", action="store_true", help="只报告，不改文件")
    args = ap.parse_args()

    nv = Novelist(Project(args.title))
    done = sorted(nv.p.state.get("done", []))
    if not done:
        print("没有已写章节")
        return 1
    lo, hi = args.a, (args.b or done[-1])
    target = nv.target_words()
    cw = nv.style.get("chapterWords")
    floor = int(cw[0]) if isinstance(cw, (list, tuple)) and len(cw) == 2 else int(target * 0.85)
    print(f"《{args.title}》验收线 {target} 字，扩写地板 {floor} 字，处理第 {lo}-{hi} 章\n")

    for n in [c for c in done if lo <= c <= hi]:
        raw = nv.p.chapter(n)
        if not raw:
            continue
        body = clean(raw)                       # 顺手清掉末尾的工作笔记
        cn = len(re.findall(r"[一-鿿]", body))
        tag = "" if cn >= floor else f" → 需扩写(缺 {target - cn} 字)"
        print(f"第{n:3d}章 {cn:5d} 字{tag}")
        if args.dry:
            continue

        if body != raw:                          # 只是清洗也要落盘
            nv.p.write(nv.p.chapter_path(n), body)

        if cn >= floor:
            continue
        grow = (f"下面这一章只有 {cn} 字，目标 {target} 字，缺 {target - cn} 字。\n"
                f"请在**不改变任何已有情节与结局**的前提下扩写到 {target} 字左右：\n"
                f"- 把一笔带过的关键场景演出来（对话、动作、交锋的来回）\n"
                f"- 给已出场的配角补上反应与小动作\n"
                f"- 补足做局/算账/谈判的具体过程，让读者跟得上推理\n"
                f"- 不要加新人物、新地点、新情节线，不要写心理总结与环境铺陈\n"
                f"- 不要在正文末尾附任何状态更新、伏笔登记、字数统计\n"
                f"禁用套话：{'、'.join(nv.blacklist()[:40])}\n"
                f"直接输出扩写后的完整正文，无前言。\n\n{body}")
        # 扩到达标为止, 最多两轮 —— 一轮补不满是常态, 模型对「缺 1400 字」
        # 的响应通常只补一半
        body2, cn2 = body, cn
        for round_ in (1, 2):
            if cn2 >= floor:
                break
            g = grow.replace(f"只有 {cn} 字", f"只有 {cn2} 字").replace(
                f"缺 {target - cn} 字", f"缺 {target - cn2} 字")
            if round_ == 2:
                g = (f"⚠️ 这是第二轮扩写，上一轮只补到 {cn2} 字仍不达标"
                     f"（下限 {floor} 字），这次必须写够。\n" + g)
            t2 = clean(call("polishing", g.replace(body, body2), max_tokens=8192).text)
            n2 = len(re.findall(r"[一-鿿]", t2))
            if not t2 or n2 <= cn2 * 1.05:
                print(f"        第{round_}轮扩写无效（{n2} 字）")
                break
            body2, cn2 = t2, n2
        if cn2 <= cn * 1.05:
            print(f"        扩写无效，保留原稿 {cn} 字")
            continue
        t2 = body2
        a = audit(t2, extra_blacklist=nv.hard_blacklist(), target_words=target,
                  check_modern=nv.anachronism_check())
        a["target_words"] = target
        a["expanded"] = True
        nv.p.write(nv.p.chapter_path(n), t2)
        nv.p.write(f"audit/{n:03d}.json", json.dumps(a, ensure_ascii=False, indent=2))
        print(f"        ✓ {cn} → {cn2} 字，得分 {a['score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

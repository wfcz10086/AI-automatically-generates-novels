#!/usr/bin/env python3
"""按检测结论定点重排细纲 —— 检测器报出来的断线，这里才是修它的地方。

检测器只报警不修等于「有生产者没消费者」：断线躺在日志里，没有任何东西
能把它变回一段有戏的细纲。本脚本把结论翻译成重排单并执行，前后章节不动。

    python3 scripts/replan.py --title 书名 [--dry] [--kind 支线断线] [--max 3]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.orchestrator import Novelist, Project      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--dry", action="store_true", help="只列重排单，不动细纲")
    ap.add_argument("--kind", default="", help="只处理某一类（支线断线/张力静默消解/阶梯停滞/承诺挨饿）")
    ap.add_argument("--max", type=int, default=0, help="最多执行几条")
    a = ap.parse_args()

    nv = Novelist(Project(a.title))
    jobs = nv.outline_repairs()
    if a.kind:
        jobs = [j for j in jobs if j["kind"] == a.kind]
    if not jobs:
        print("没有检出需要重排的地方")
        return 0

    print(f"《{a.title}》检出 {len(jobs)} 条重排单\n")
    for i, j in enumerate(jobs, 1):
        print(f"{i}. [{j['kind']}] 第 {'、'.join(map(str, j['chapters']))} 章")
        print(f"   {j['demand'][:150]}")
    if a.dry:
        return 0

    todo = jobs[:a.max] if a.max else jobs
    print(f"\n开始重排 {len(todo)} 条…\n")
    ok = 0
    for i, j in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {j['kind']} 第 {j['chapters']} 章 …", flush=True)
        n = nv.replan_outline(j["chapters"], j["demand"])
        print(f"        重排 {n}/{len(j['chapters'])} 章")
        ok += n
    print(f"\n共重排 {ok} 章。建议再跑一次本脚本 --dry 复检。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

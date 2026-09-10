#!/usr/bin/env python3
"""把从原著逆向出来的参数**回写**进引擎层。

今天这些数是我读完 L2a 手抄进包的：误读寿命我原以为 25 章、实证 1-3 章；
推进燃料我原以为误读占七成、实证三成。手抄一次就固化一次错误，而且下次谁也
不知道包里的数是哪来的。这个脚本把那一步变成可重复的：

  derive_mainline → 每本书出一份 L4_calibration.json
  calibrate       → 多本书取共识 → 与 packs/engine/core.json 逐项对比 → 回写

规矩：
- 只写**引擎层已有且被代码消费**的字段。校准不许发明新参数
  （否则又长出一堆没人读的死参数，见单测「包里不许有无人消费的字段」）。
- 默认只打印差异不落盘，--apply 才写。
- 每次回写都记 provenance：哪本书、哪次推导、什么时候。

用法:
  python3 scripts/calibrate.py                       # 看差异
  python3 scripts/calibrate.py --apply               # 回写
  python3 scripts/calibrate.py --books ds-0910-1741  # 只用某几次推导
"""
from __future__ import annotations
import argparse, json, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "packs/engine/core.json"
MAINLINE = ROOT / "reports/mainline"

#: 可校准字段 → (类型, 合法范围)。不在这张表里的一律忽略。
ALLOW = {
    "misreadLifecycle.发酵章数": ("pair", 1, 40),
    "misreadLifecycle.上限":     ("int", 1, 60),
    "misreadLifecycle.戳破方式": ("str", 0, 0),
    "advanceFuel.账目变动":      ("frac", 0, 1),
    "advanceFuel.新误读":        ("frac", 0, 1),
    "advanceFuel.旧误读发酵":    ("frac", 0, 1),
    "advanceFuel.外部事件":      ("frac", 0, 1),
    "advanceFuel.新人物":        ("frac", 0, 1),
    "advanceFuel.承接率":        ("frac", 0, 1),
    "eventSpan.span":            ("pair", 1, 60),
    "obsolete.afterUses":        ("int", 2, 10),
    "reshell.tailChapters":      ("int", 1, 20),
    "worldTurn.every":           ("int", 2, 30),
}


def dig(d, path):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def put(d, path, v):
    ks = path.split(".")
    for k in ks[:-1]:
        cur = d.setdefault(k, {})
        if not isinstance(cur, dict):
            return
        d = cur
    d[ks[-1]] = v


def ok(kind, lo, hi, v):
    if kind == "str":
        return isinstance(v, str) and len(v.strip()) > 4
    if kind == "pair":
        return (isinstance(v, list) and len(v) == 2
                and all(isinstance(x, (int, float)) and lo <= x <= hi for x in v)
                and v[0] <= v[1])
    if kind == "int":
        return isinstance(v, (int, float)) and lo <= v <= hi
    if kind == "frac":
        return isinstance(v, (int, float)) and lo <= v <= hi
    return False


def consensus(kind, vals):
    """多本书取共识：数值取中位数，区间逐端取中位数，文字取最长的一条。"""
    if kind == "str":
        return max(vals, key=len)
    if kind == "pair":
        return [round(statistics.median(v[i] for v in vals)) for i in (0, 1)]
    if kind == "int":
        return round(statistics.median(vals))
    return round(statistics.median(vals), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写进 core.json")
    ap.add_argument("--books", nargs="*", default=None, help="只用这几次推导的目录名")
    a = ap.parse_args()

    files = sorted(MAINLINE.glob("*/L4_calibration.json"))
    if a.books:
        files = [f for f in files if f.parent.name in a.books]
    if not files:
        print(f"没有 L4_calibration.json。先跑 scripts/derive_mainline.py（产物在 {MAINLINE}）")
        return 1
    srcs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    print(f"取 {len(srcs)} 本书的校准: " + "、".join(s.get("_book", "?") for s in srcs) + "\n")

    core = json.loads(CORE.read_text(encoding="utf-8"))
    changes, skipped = [], []
    for path, (kind, lo, hi) in ALLOW.items():
        # 必须**整条路径**都已存在。只查第一段是不够的: eventSpan 在引擎层是个
        # 布尔开关(true), 写 eventSpan.span 会把它变成字典 —— 校准脚本自己
        # 就在发明新参数, 正是它该防的事。
        if dig(core, path) is None:
            skipped.append(f"{path}（引擎层没有这一项，校准不许发明新参数）")
            continue
        vals = []
        for s in srcs:
            v = dig(s, path)
            if v is not None and ok(kind, lo, hi, v):
                vals.append(v)
        if not vals:
            continue
        new = consensus(kind, vals)
        old = dig(core, path)
        if old != new:
            spread = ("" if len(vals) < 2 else
                      f"  ← 各书: {'、'.join(str(v) for v in vals)}")
            changes.append((path, old, new, spread))

    if skipped:
        print("跳过：")
        for x in skipped:
            print(f"  · {x}")
        print()
    if not changes:
        print("引擎层与原著实测一致，无需改动。")
        return 0
    print(f"{'字段':28s}{'现在':>22s}   →  {'实测':<22s}")
    for path, old, new, spread in changes:
        so, sn = str(old)[:20], str(new)[:20]
        print(f"{path:28s}{so:>22s}   →  {sn:<22s}{spread}")

    if not a.apply:
        print("\n（只是预览。要真的写进去加 --apply）")
        return 0
    for path, _old, new, _s in changes:
        put(core, path, new)
    core["_校准"] = {
        "来源": [f.parent.name for f in files],
        "书": [s.get("_book", "?") for s in srcs],
        "改了": [c[0] for c in changes],
        "说明": ("这些数由 scripts/calibrate.py 从原著逆向结果回写，不要手改。"
                 "要调就重跑 derive_mainline 再 calibrate。"),
    }
    CORE.write_text(json.dumps(core, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {CORE}（{len(changes)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

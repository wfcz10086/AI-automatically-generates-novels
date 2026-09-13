#!/usr/bin/env python3
"""熔断自愈：守护的 HALT 处理器 —— 修得动就修，修不动就回炉发散，两轮不过才等人。

用法: python3 scripts/heal_halt.py <书名>

三级递进（每级都比上一级贵，能便宜治好就不动大刀）：
  一 replace 返修     队列里的章逐个重写（冲突/时间线类升 replace）
  二 reroll 回炉      返修后仍连片坏 → 回滚到坏段起点, 细纲重新三候选发散,
                      交还守护重写整段。种子和合同树不动 —— 发散来自
                      温度 + 多候选, 这正是「自动种子扩散」该干的事。
  三 留给人工         同一段回炉满 2 次仍坏 → HALT.md 标 MANUAL, 守护不再
                      自动处理。到这一步多半是种子/合同本身要人改。

成功路径的最后一步是**删掉 HALT.md** —— 守护看到它消失就自动续跑。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.orchestrator import Project, Novelist          # noqa: E402
from server import rollback as rb                           # noqa: E402

MAX_REROLL_PER_SEGMENT = 2


def say(m):
    print(f"[heal {time.strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    p = Project(title)
    halt = p.dir / "HALT.md"
    if not halt.exists():
        say("没有 HALT, 无事可做")
        return 0
    if "MANUAL" in halt.read_text(encoding="utf-8"):
        say("HALT 已标 MANUAL, 等人处理")
        return 0

    nv = Novelist(p)

    # ── 一级: replace 返修队列 ──
    q = p._load("repair_queue.json", [])
    say(f"一级返修: 队列 {len(q)} 条 {[x.get('ch') for x in q]}")
    rest = []
    for item in q:
        ch, note = item["ch"], str(item.get("note") or "")
        mode = ("replace" if any(w in note for w in
                                 ("冲突", "时间线", "矛盾", "跳变", "该拦"))
                else "polish")
        try:
            r = nv.rewrite_chapter(ch, mode=mode, note=note)
            st = r.get("status") or "?"
            say(f"  第{ch}章 {mode} → {r.get('score')} / {st}")
            if st in ("需人工", "失败"):
                rest.append(item)
        except Exception as e:
            say(f"  第{ch}章 崩: {type(e).__name__}: {e}")
            rest.append(item)
    p.write("repair_queue.json", json.dumps(rest, ensure_ascii=False, indent=2))

    if not rest:
        halt.unlink(missing_ok=True)
        say("一级返修全过, 删 HALT, 守护自动续跑")
        return 0

    # ── 二级: 回炉发散 ──
    n0 = min(int(x["ch"]) for x in rest)
    hs_f = p.dir / "heal_state.json"
    hs = json.loads(hs_f.read_text(encoding="utf-8")) if hs_f.exists() else {}
    # 按段计数会被**起点漂移**绕过: 实测第一次回滚到 8、第二次到 7,
    # heal_state 里 {"8":1,"7":1} 两个键各自没到上限, 乒乓可以打到天亮。
    # 改成双闸: 同段两次(旧规矩) + **全书回炉总数**(近 6 次自愈里回炉 ≥3
    # 就是在打乒乓, 不管起点漂到哪) —— 后者才是真的止损线。
    key = str(n0)
    hs[key] = int(hs.get(key) or 0) + 1
    hist = list(hs.get("_history") or [])
    hw = len(p.state.get("done", []))          # 高水位: 现有完成章数
    prev = hist[-1] if hist else None
    hist.append({"n0": n0, "hw": hw, "at": time.strftime("%m%d_%H%M")})
    hs["_history"] = hist[-10:]
    hs_f.write_text(json.dumps(hs, ensure_ascii=False, indent=1), encoding="utf-8")

    # 止损看**方向**, 不是次数。实测一夜两轮回炉(n0: 7→14, 章数 7→27) ——
    # 那是磨着前进, 不是乒乓; 按「近 6 次自愈回炉 ≥3」一刀切, 第三轮就会把
    # 正常前进误判成乒乓标 MANUAL。乒乓的定义是**没有进度**:
    #   n0 不再前进(回滚点倒退或原地), 或距上次回炉净增章数 < 3。
    ping_pong = bool(prev) and (
        n0 <= int(prev.get("n0") or 0)
        or hw - int(prev.get("hw") or 0) < 3)

    if hs[key] > MAX_REROLL_PER_SEGMENT or ping_pong:
        # ── 三级: 真的等人 ──
        why = (f"同段回炉 {hs[key]-1} 次" if hs[key] > MAX_REROLL_PER_SEGMENT
               else f"回炉没有进度(回滚点 {prev.get('n0')}→{n0}, "
                    f"章数 {prev.get('hw')}→{hw})，在打乒乓")
        halt.write_text(
            halt.read_text(encoding="utf-8")
            + f"\n\nMANUAL: {why}，仍连片被拦。"
            f"\n发散治不了的多半是种子或合同本身的问题 —— 看 canon_conflicts.json"
            f" 里被反复推翻的事实，改种子/树或认可正文，然后删掉本文件。\n",
            encoding="utf-8")
        say(f"第 {n0} 章段已回炉 {hs[key]-1} 次仍坏, 标 MANUAL 等人")
        return 1

    say(f"二级回炉: 修不动的 {[x['ch'] for x in rest]}, "
        f"回滚到第 {n0} 章之前重新发散（本段第 {hs[key]} 次）")
    rb.rollback_to(p, n0, mem=p.mem, log=say)
    halt.unlink(missing_ok=True)
    say("回滚完成, 删 HALT —— 守护会拉起长跑, 细纲三候选重排、正文重写")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

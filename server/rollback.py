"""回炉发散：连片坏段回滚到起点，从合同树重新发散重写。

—— 为什么要有这个文件 ——

返修有三档，前两档早就有了，第三档一直缺着：

  polish   只改语言          治文风病
  replace  单章重写          治单章的结构病
  reroll   回滚整段、重新发散  治**牵连病**

牵连病长这样（实测第 4~12 章）：第 4 章把时间线写错，第 8~12 章全建在
错的时间线上 —— 单章 replace 怎么改都跟邻章矛盾，改了 8 章还 0 分，
因为病根不在这一章。唯一的治法是回到坏段起点，把这一段的细纲**重新
三候选发散**（种子和合同树没动，发散来自温度 + 多候选），再写。

回滚是纯状态操作，全归程序：正文/细纲/台账/伏笔/记忆/摘要，凡是
chapter >= n0 的一律清掉（正文先备份）。清不干净就等于没回滚 ——
残留的旧事实会立刻把新写的章再拦一遍。
"""
from __future__ import annotations

import json
import shutil
import time
from typing import Any, Dict, List, Tuple


# ── 纯函数（可单测）：各类状态按章号截断 ──

def prune_canon(canon: List[Dict[str, Any]], n0: int) -> List[Dict[str, Any]]:
    """chapter >= n0 的事实清掉。chapter=0 的种子事实永远保留。"""
    return [c for c in canon if int(c.get("chapter") or 0) < n0]


def prune_state(state: Dict[str, Any], n0: int) -> Dict[str, Any]:
    state = dict(state)
    state["done"] = [c for c in state.get("done", []) if int(c) < n0]
    state["current"] = max([0] + state["done"])
    sm = state.get("summaries") or {}
    state["summaries"] = {k: v for k, v in sm.items()
                          if not str(k).isdigit() or int(k) < n0}
    return state


def prune_outlines(co: Dict[str, str], n0: int) -> Dict[str, str]:
    return {k: v for k, v in co.items()
            if not str(k).isdigit() or int(k) < n0}


def prune_queue(q: List[Dict[str, Any]], n0: int) -> List[Dict[str, Any]]:
    """坏段里的返修单全部作废 —— 那些章马上就不存在了。"""
    return [x for x in q if int(x.get("ch") or 0) < n0]


def replay_accounts(led: Dict[str, Dict[str, Any]], n0: int) -> Dict[str, Dict[str, Any]]:
    """硬账按流水回放到 n0 之前的最后状态。"""
    out = {}
    for k, e in (led or {}).items():
        log = [l for l in e.get("log", []) if int(l.get("chapter") or 0) < n0]
        if not log:
            continue
        e2 = dict(e)
        e2["log"] = log
        e2["value"] = log[-1]["value"]
        e2["chapter"] = log[-1]["chapter"]
        out[k] = e2
    return out


def prune_conflicts(d: Dict[str, Any], n0: int) -> Dict[str, Any]:
    out = {}
    for k, v in (d or {}).items():
        chs = [c for c in v.get("chapters", []) if int(c) < n0]
        if chs:
            out[k] = dict(v, chapters=chs)
    return out


# ── 有副作用的整合入口 ──

def rollback_to(project, n0: int, mem=None, log=print) -> Dict[str, Any]:
    """把一本书回滚到「第 n0 章尚未写」的状态。正文先备份，永不覆盖丢失。"""
    ts = time.strftime("%m%d_%H%M%S")
    bak = project.dir / ".ckpt" / f"rollback_{ts}_ch{n0}"
    bak.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in sorted((project.dir / "chapters").glob("*.md")):
        try:
            if int(f.name[:3]) >= n0:
                shutil.move(str(f), str(bak / f.name))
                moved += 1
        except ValueError:
            continue
    for f in sorted((project.dir / "audit").glob("*.json")) if (project.dir / "audit").exists() else []:
        try:
            if int(f.name[:3]) >= n0:
                shutil.move(str(f), str(bak / ("audit_" + f.name)))
        except ValueError:
            continue

    co = project._load("chapter_outlines.json", {})
    project.write("chapter_outlines.json",
                  json.dumps(prune_outlines(co, n0), ensure_ascii=False, indent=1))
    project.write("canon.json", json.dumps(
        prune_canon(project._load("canon.json", []), n0),
        ensure_ascii=False, indent=2))
    project.write("canon_conflicts.json", json.dumps(
        prune_conflicts(project._load("canon_conflicts.json", {}), n0),
        ensure_ascii=False, indent=2))
    led = replay_accounts(project._load("accounts.json", {}), n0)
    if led:
        project.write("accounts.json", json.dumps(led, ensure_ascii=False, indent=1))
    project.write("repair_queue.json", json.dumps(
        prune_queue(project._load("repair_queue.json", []), n0),
        ensure_ascii=False, indent=2))
    project.state.update(prune_state(project.state, n0))
    project.save()

    # 记忆与伏笔: 残留的旧事实会立刻把新写的章再拦一遍, 必须清干净
    if mem is not None:
        try:
            for i in range(n0, n0 + moved + 60):
                mem.db.execute("DELETE FROM mem WHERE ref=?", (f"ch{i}",))
            mem.db.execute("DELETE FROM foreshadow WHERE planted>=?", (n0,))
            mem.db.execute(
                "UPDATE foreshadow SET resolved=0, resolved_at=NULL "
                "WHERE resolved_at>=?", (n0,))
            mem.db.commit()
        except Exception as e:
            log(f"[rollback] 记忆清理不完整: {type(e).__name__}: {e}")

    log(f"[rollback] 已回滚到第 {n0} 章之前：{moved} 章正文移入 {bak.name}/，"
        f"细纲/台账/硬账/伏笔/摘要同步截断")
    return {"backup": str(bak), "chapters_moved": moved}

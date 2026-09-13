"""硬账台账：子弹这类**可计数**的状态，程序记账、程序对数。

—— 为什么要有这个文件 ——

用户通读 18 章抓出的最硬一条伤：子弹第 1 章开枪后 119 发，第 5、8 章反复
确认 119，第 15 章突然「还有十二发」——119→12，中间没有任何消耗情节。

链条上每一环都有人管，唯独数目没有：
  · 台账(canon)管不可逆事实 —— 而我刚给它加的 _STATEY 恰恰**拒收**
    「剩余 N 发」这类会变的数量（拒得对：数量进不可逆账就是下毒）
  · 合同树的 accounts 管里程碑两端 —— 根账本里明明写着「沙漠之鹰，120发」，
    但没有任何东西把它带到逐章层面
  · 评审管语义矛盾 —— 数数不是它的强项

数量是**最该程序管**的东西：加减是算术，对数是正则。分工：
  程序记当前值 → 注入提示词（这是硬账，写数字必须与此一致）
  → 评审顺带报本章增减（语义活：判断「开了一枪」归模型）
  → 程序应用增减、并用正则对正文里出现的数字 —— 对不上就记违约
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
       "六": 6, "七": 7, "八": 8, "九": 9}


def cn_num(s: str) -> Optional[int]:
    """中文/阿拉伯数字 → int。支持到千位（子弹/银两够用）。解析不了返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN:
            cur = cur * 10 + _CN[ch] if cur else _CN[ch]
        elif ch == "十":
            cur = (cur or 1) * 10
        elif ch == "百":
            cur = (cur or 1) * 100
        elif ch == "千":
            cur = (cur or 1) * 1000
        else:
            return None
        if ch in "十百千":
            total += cur
            cur = 0
    return total + cur


#: 根账本条目形如「沙漠之鹰，120发」「万贯现银」—— 抠出 (数, 单位)。
_QTY = re.compile(r"([0-9]+|[零〇一二两三四五六七八九十百千]+)\s*([发枚颗粒贯两文钱斤石]|发子弹)")


def seed_from_tree(tree_path) -> Dict[str, Dict[str, Any]]:
    """从合同树根节点的 accounts 里抠出可计数项。抠不出数的不进硬账。"""
    try:
        root = json.loads(open(tree_path, encoding="utf-8").read()).get("R") or {}
        acc = (root.get("entry") or {}).get("accounts") or {}
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in acc.items():
        m = _QTY.search(str(v))
        if not m:
            continue
        n = cn_num(m.group(1))
        if n is None:
            continue
        # 「万贯」这类虚指不进 —— 只有精确数才对得了数
        out[k] = {"item": k, "desc": str(v)[:40], "value": n,
                  "unit": m.group(2)[:1], "chapter": 0,
                  "log": [{"chapter": 0, "value": n, "why": "开局"}]}
    return out


def brief(led: Dict[str, Dict[str, Any]]) -> str:
    """注入提示词的硬账块。"""
    if not led:
        return ""
    lines = ["⚙【硬账·程序记账，正文里写到数字必须与此一致】"]
    for k, e in led.items():
        lines.append(f"　{k}（{e['desc']}）：当前 **{e['value']} {e['unit']}**")
    lines.append("　增减只能来自本章明写的事件（开一枪减一发、花一笔减一笔），"
                 "不许凭空跳数。不确定就别写具体数字。")
    return "\n".join(lines)


def apply_changes(led: Dict[str, Dict[str, Any]], changes: List[Dict[str, Any]],
                  chapter: int) -> List[str]:
    """应用评审报来的增减。返回日志行。"""
    logs = []
    for c in changes or []:
        if not isinstance(c, dict):
            continue
        item = str(c.get("item") or "").strip()
        try:
            delta = int(c.get("delta"))
        except Exception:
            continue
        hit = next((k for k in led if item and (item in k or k in item)), None)
        if not hit or delta == 0:
            continue
        e = led[hit]
        e["value"] = max(0, e["value"] + delta)
        e["chapter"] = chapter
        e["log"].append({"chapter": chapter, "value": e["value"],
                         "why": str(c.get("why") or "")[:40]})
        logs.append(f"硬账 {hit}: {delta:+d} → {e['value']} {e['unit']}"
                    f"（{str(c.get('why') or '')[:24]}）")
    return logs


def check_prose(led: Dict[str, Dict[str, Any]], text: str) -> List[str]:
    """正文里出现的数字与台账对数。对不上就是 119→12 那类硬伤。

    只查「数字+单位」且 24 字内出现台账物名的 —— 别把无关的「十二发炮仗」
    也拉来对子弹的账。
    """
    bad = []
    t = text or ""
    for k, e in led.items():
        # 物名取台账键与描述里的 2 字以上词
        names = [k] + re.findall(r"[一-鿿]{2,4}", e.get("desc", ""))
        for m in _QTY.finditer(t):
            if m.group(2)[0] != e["unit"]:
                continue
            around = t[max(0, m.start() - 24):m.end() + 8]
            if not any(nm and nm in around for nm in names if len(nm) >= 2):
                continue
            n = cn_num(m.group(1))
            if n is None:
                continue
            # 允许 ±1: 本章刚消耗的那一笔可能写的是消耗后的数
            if abs(n - e["value"]) > 1:
                bad.append(f"{k}：正文写「{m.group(0)}」，台账是 {e['value']} "
                           f"{e['unit']}（第{e['chapter']}章后）—— 数目跳变")
    return bad

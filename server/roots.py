"""种子根系：从种子一次性发散出素材池，程序按批配发，用过即焚。

—— 为什么要有这个文件 ——

150 章的实测病灶：仵作何九叔一条线占了 43% 的章节，章节名连成串是
「验尸笔·回响 → 断章 → 残片 → 断笔·真伪 → 断笔·回响」—— 同一份证据
翻案四十章。用户的判词最准：「每次都在**加深**而不是**推进**。」

为什么模型会这样？因为**加深已有冲突比制造新冲突省力得多**，而它每批
临场编剧情时，"想"被最近上下文支配 —— 最近在验尸，就继续验尸。
三候选也救不了：三稿从同一段上下文生成，温度再高也只是同一个想法的
三种写法，不是三个方向。

缺的不是约束，是**素材**。所以：

    开书时  模型从种子一次性发散出素材池（这是它擅长的：想点子）
    每一批  程序挑几条没用过的配发下去（这是程序擅长的：记账、不重复）
    用过的  标记章号，永不再发

这就是「可控的发散」：**程序控制用什么，模型决定怎么用**。主干（里程碑
出口合同）钉死不变，枝叶（怎么走到那里）每批都不一样。

六类素材，对应网文推进的六种动力：
    conflict  新冲突源：谁为什么跟主角过不去
    card      主角能打的牌：他手上有什么可以兑现
    reversal  可翻的脸：谁的立场会变、什么秘密会露
    cost      要付的代价：赢也得掉块肉
    arena     新舞台：换个地方/圈子，物理上断掉旧线
    device    新手段：可用一次的东西（不是金手指，是道具与做局）
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

KINDS = {
    "conflict": "新冲突源（谁为什么跟主角过不去，要具体到人和利益）",
    "card": "主角能打的牌（他手上已有或将有的东西，兑现时能换来什么）",
    "reversal": "可翻的脸（谁的立场会变／什么秘密会露，要写明触发条件）",
    "cost": "要付的代价（赢下来也得掉一块肉：钱、人、名声、身体、把柄）",
    "arena": "新舞台（换个地方或圈子，物理上断掉旧线的那种）",
    "device": "新手段（可用一次的道具或做局方式，不是金手指）",
}


def p_roots(seed: str, chain: str, n_each: int = 10) -> str:
    return f"""下面是一本长篇小说的种子和它的主线骨架。请从中**发散**出一批可用素材。

── 种子 ──
{seed}

── 主线骨架（这是不变的主干，素材必须服务于它，不许改它）──
{chain}

发散六类，每类 {n_each} 条。要求：

1. **每条都要能单独支撑一到两章**，不是一句概念。写清楚：谁、因为什么、
   会做什么、主角要怎么应对。
2. **互相之间尽量不同源** —— 六条冲突不许都来自同一个人、同一件事。
   一本书拖垮的典型死法是：同一份证据反复翻案四十章。
3. 服从种子的世界观与红线：不许出现现代词汇、不许违背已定死的设定。
4. 用一句话写，40-70 字，具体到能直接排纲。

只输出 JSON（不要代码围栏、不要解释）：
{{"conflict":["…","…"],"card":["…"],"reversal":["…"],
  "cost":["…"],"arena":["…"],"device":["…"]}}"""


def parse(raw: str, n_each: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """解析成带 id 与 used 标记的素材池。"""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for kind in KINDS:
        items = d.get(kind) or []
        if not isinstance(items, list):
            continue
        rows = []
        for x in items[:n_each * 2]:
            t = str(x).strip()
            if len(t) < 8:
                continue
            rows.append({"id": f"{kind[:2]}{len(rows)+1}", "what": t[:160],
                         "used": None})
        if rows:
            out[kind] = rows
    return out


def load(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(path: Path, pool: Dict[str, List[Dict[str, Any]]]) -> None:
    path.write_text(json.dumps(pool, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def unused(pool: Dict[str, List[Dict[str, Any]]],
           kind: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for k, rows in pool.items():
        if kind and k != kind:
            continue
        out += [dict(r, kind=k) for r in rows if not r.get("used")]
    return out


def pick(pool: Dict[str, List[Dict[str, Any]]], start: int,
         want: int = 3) -> List[Dict[str, Any]]:
    """给这一批挑几条素材。**程序挑，不问模型** —— 模型会挑它最熟的那类，
    等于没换方向。

    挑法：按类轮转（这一批 conflict，下一批 reversal…），每类取最早未用的。
    轮转用批次序号定，与内容无关 —— 保证六类都被用上，不会全是冲突。
    """
    kinds = list(KINDS)
    got: List[Dict[str, Any]] = []
    off = (start // 5) % len(kinds)          # 每批(5章)转一格
    for i in range(len(kinds)):
        k = kinds[(off + i) % len(kinds)]
        free = [r for r in (pool.get(k) or []) if not r.get("used")]
        if free:
            got.append(dict(free[0], kind=k))
        if len(got) >= want:
            break
    return got


def mark_used(pool: Dict[str, List[Dict[str, Any]]],
              picks: List[Dict[str, Any]], start: int) -> None:
    ids = {(p["kind"], p["id"]) for p in picks}
    for k, rows in pool.items():
        for r in rows:
            if (k, r["id"]) in ids and not r.get("used"):
                r["used"] = start


def brief(picks: List[Dict[str, Any]], pool_left: int) -> str:
    """写进格式表之外的约束块。说清楚这是**必须用掉**的，不是备选。"""
    if not picks:
        return ""
    out = ["🌱 【本批必须动用的新素材·从种子根系里配发，用一条少一条】"]
    for p in picks:
        out.append(f"　[{p['id']}] {KINDS[p['kind']].split('（')[0]}：{p['what']}")
    out.append(f"　这几条**本批之内必须真的用上**（落到剧情条里，不是提一嘴）。"
               f"　素材池还剩 {pool_left} 条未用。")
    out.append("　⚠ 用新素材不等于开新坑：它们要服务于本节里程碑的出口，"
               "把主线往前推，而不是又开一条平行线。")
    return "\n".join(out)

def score(pool: Dict[str, List[Dict[str, Any]]]) -> float:
    """三候选选优的打分器。程序打分, 不问模型。

    看三件可数的事:
      · 六类齐不齐 —— 缺一类就少一种推进动力
      · 条数够不够 —— 太少撑不到全书
      · **是否同源** —— 最要紧的一项。一本书拖垮的典型死法是所有冲突都
        来自同一个人/同一件事(何九叔那条线占了 43% 章节)。拿人名与关键
        名物的重复率反着算: 越集中扣得越狠。
    """
    if not pool:
        return -1e9
    kinds = sum(1 for k in KINDS if pool.get(k))
    total = sum(len(v) for v in pool.values())
    words = Counter()
    for rows in pool.values():
        for r in rows:
            seen = set()
            for run in re.findall(r"[一-鿿]{2,}", r.get("what", "")):
                for i in range(len(run) - 1):
                    seen.add(run[i:i + 2])
            words.update(seen)
    top = sum(k for _w, k in words.most_common(5))
    concentration = top / max(1, total)      # 越大越同源
    return kinds * 10 + min(total, 60) * 0.5 - concentration * 8


def closing_mode(n: int, node_start: int, node_end: int,
                 tail_ratio: float = 0.3) -> bool:
    """本章是否进入**收口模式**：一节的最后 tail_ratio 不再配发新素材。

    这是「最终剧情可控」的真正保障。只发散不收口的下场是：一路开新坑，
    到了节点末尾收不回来，出口合同对不上 —— 那才是真失控。
    所以一节切成两段：
        前 70%  放开发散（每批配发新素材，剧情多变）
        后 30%  停止配发，强制收口（把本节开的坑收掉，对齐出口合同）
    """
    span = max(1, node_end - node_start + 1)
    return (n - node_start + 1) > span * (1 - tail_ratio)


def closing_brief(node, left_chapters: int) -> str:
    """收口令。带着本节出口合同的原文 —— 收到哪里去, 不能靠模型猜。"""
    ex = node.exit.brief(500) if hasattr(node, "exit") else ""
    out = [f"🔚 【收口模式·本节还剩 {left_chapters} 章，不再开新线】",
           "　本节开过的坑（人物的承诺、没兑现的账、悬着的疑点）"
           "**必须在这几章里了结**：给结果、给代价、或明写它作废。"]
    if ex:
        out.append("　出这一节时世界必须是这样（逐条对齐，这是硬合同）：")
        out.append("　" + ex)
    out.append("　⚠ 这几章**不许引入新的敌人、新的地盘、新的秘密** —— "
               "新东西留给下一节开。这一节只做一件事：把账结清。")
    return "\n".join(out)

"""叙事停滞检测：同一条线反复翻案，不是伏笔，是原地打转。

—— 为什么要有这个文件 ——

用户通读 66 章的结论最扎心的一条：仵作何九叔一个配角，**29/68 章有他**
（43%），章节名连成串是「验尸笔·回响 → 验尸笔·断章 → 验尸笔·残片 →
断笔·真伪 → 断笔·回响 → 乞丐·验尸」—— 同一份尸检证据被翻案、再反转、
再翻案，横跨四十多章。「每次都在**加深**而不是**推进**。」

合同树管不到这一层：R.1 说「断死局」、R.2 说「沧州洗白」，节点内部翻来
覆去验尸四十章，出口合同照样能对上。里程碑是**节点之间**的闸，
节点**内部**的停滞没有任何人看。

程序判得了的两件事（都是纯计数，不做语义推断）：
  · 配角戏份失衡：一个非主角名字出现在过半章节里
  · 母题复读：同一组关键词在连续 N 章的章节名/一句话里反复出现

判不了的（归模型与人）：这条线该不该收、怎么收。程序只负责**指出来**，
并把它写进下一批细纲的约束：这一批必须让它落地或退场。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Tuple


def name_hotspots(chapters: Dict[int, str], roster: List[str],
                  protagonist: str = "", ratio: float = 0.35,
                  window: int = 24) -> List[Tuple[str, int, int]]:
    """配角出场率过高的名单。返回 [(名字, 出现章数, 统计章数)]。

    只看**最近 window 章** —— 全书统计会把早期已经收掉的线一直算进来。
    主角不算（他本来就该章章在）。

    名单不能只信 roster: 实测这本书的 roster 只剩三个名字(林远/哑巴/李固),
    而拖住主线的何九叔、武松、潘金莲一个都不在 —— roster 被某轮回滚清空后
    没重建, 于是按它查热点查了个空。改成 roster **并上**从正文说话归属
    自动认出的名字(tics.auto_names, 已验证过的那套)。
    """
    ns = sorted(chapters)[-window:]
    if len(ns) < 8:
        return []
    from .tics import auto_names
    names = set(x for x in roster if x) | auto_names(chapters[n] for n in ns)
    # auto_names 允许名字与「道」之间隔 0~4 字, 于是切出「林远没」「林远低」
    # 「他知」「或者」这类片段。两道滤:
    #   · 含主角名的都是主角的碎片, 不是配角
    #   · 含虚词/动词字的不是名字(真名如 何九叔/潘金莲/柴进/武松 全不含)
    _JUNK = set("没知低是在和与也就都而但把被给从对这那之其于及或者了的"
                "说道问看想听走来去出进过还又再很太不无有会能要个们")
    out = []
    for nm in sorted(names):
        if not nm or nm == protagonist or len(nm) < 2:
            continue
        if protagonist and (protagonist in nm or nm in protagonist):
            continue
        if any(ch in _JUNK for ch in nm):
            continue
        hit = sum(1 for n in ns if nm in (chapters[n] or ""))
        if hit >= max(4, int(len(ns) * ratio)):
            out.append((nm, hit, len(ns)))
    return sorted(out, key=lambda x: -x[1])


def motif_repeat(titles: Dict[int, str], window: int = 16,
                 cap: int = 4) -> List[Tuple[str, int]]:
    """章节名里反复出现的母题词。

    「验尸笔·回响 / 验尸笔·断章 / 断笔·真伪 / 断笔·回响」—— 母题词
    (验尸/断笔/回响) 在窗口里出现 cap 次以上就是复读。
    2 字词即可，章节名本来就短。
    """
    ns = sorted(titles)[-window:]
    if len(ns) < 8:
        return []
    c = Counter()
    for n in ns:
        seen = set()
        for run in re.findall(r"[一-鿿]{2,}", titles[n] or ""):
            for L in (3, 2):
                for i in range(len(run) - L + 1):
                    seen.add(run[i:i + L])
        c.update(seen)
    return [(w, k) for w, k in c.most_common(12) if k >= cap][:6]


def brief(hot: List[Tuple[str, int, int]],
          motif: List[Tuple[str, int]]) -> str:
    """写进下一批细纲的约束。说清楚**要它做什么**，不是只报警。"""
    if not hot and not motif:
        return ""
    out = ["⛔ 【这条线在原地打转·本批必须了断】"]
    for nm, hit, tot in hot[:2]:
        out.append(f"　「{nm}」最近 {tot} 章里出现了 {hit} 章 —— "
                   f"配角戏份压过了主线。本批给他一个**了断**："
                   f"兑现他手上的东西、让他退场、或让他彻底反水后离开视野；"
                   f"不许再出现「又翻一次案／又想起一个细节」这类加深戏。")
    if motif:
        ws = "、".join(f"{w}({k}次)" for w, k in motif[:4])
        out.append(f"　章节名反复出现同一组母题：{ws} —— "
                   f"这说明连着好几章在同一件事上打转。本批的章节名与事件"
                   f"必须换一件事做，主线往下一个里程碑推。")
    out.append("　判据：同一份证据/同一个疑点**不许第三次翻案**。"
               "要么这一批给出定论，要么让它彻底失效（作废令）。")
    return "\n".join(out)

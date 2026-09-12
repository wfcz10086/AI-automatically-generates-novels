"""叙述装置的复用检测：同一个比喻不许全书反复用。

—— 为什么要有这个文件 ——

用户读完 46 章指出来的：「像砂纸磨过生锈的铁管」「烧红的铁钎在骨缝里搅拌」
「风箱般的嘶鸣」反复出现。实测：

    砂纸 24 次 ／ 风箱 13 次 ／ 蚯蚓 7 次 ／ 烙铁 6 次 ／ 铁钎 5 次

这类东西靠提示词治不了，原因很实在：**模型写第 40 章时不知道第 3 章用过
「砂纸」**。你在提示词里写一百遍「比喻不要重复」，它也只能在本章之内不重复。

程序知道。所以这件事的分工是：
    程序统计已经用过的  →  程序把它们写进提示词当禁用表  →  模型换别的

这跟「势力名归模型认」正好相反, 两者的判断标准是同一条：
    哪些词是势力名        开放语义 → 归模型
    「砂纸」出现过几次     纯计数   → 归程序
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Tuple

#: 不猜「什么是比喻」—— 那是语义判断, 正则猜比喻在这本书上实测漏掉了主力:
#: 「砂纸」24 次全是「嗓音砂纸般刮出」「砂纸磨过铁管」这类, 不走「像…」结构,
#: 引导词法一个都没抓到, 反倒抓出「是从」这种垃圾。
#: 程序判得准的是另一件事: **什么样的具体措辞在反复出现** —— 纯计数。
#: 做法: 全书数 3~5 字 n-gram, 高频的再过两道筛(含具体物象字/不是人名地名),
#: 剩下的就是「叙述拐杖」—— 不区分它是不是比喻, 用滥的白描一样该换。

#: n-gram 里含这些字的才算「物象/体感」措辞 —— 这是**开放集合上的降噪**,
#: 不追求全, 只追求准: 进禁用表的东西必须真的是拐杖, 抓错比漏掉糟。
_CONCRETE = set("砂纸铁钎钳风箱烙蚯蚓钢缆锈管泥狗尸墨潮涌浆糊雷鼓刀割针扎"
                "火烧冰锥磨刮搅绞碾锤砸筛糠蛇虫兽嘶吼腥锈甜臭麻痹胀裂")

_STOP_RUN = re.compile(r"[^一-鿿]")


def _grams(text: str, lo: int = 3, hi: int = 5) -> Iterable[str]:
    for run in _STOP_RUN.split(text or ""):
        for n in range(lo, hi + 1):
            for i in range(len(run) - n + 1):
                yield run[i:i + n]


def overused(texts: Iterable[str], cap: int = 3, top: int = 20,
             names: Iterable[str] = ()) -> List[Tuple[str, int]]:
    """全书反复出现的体感措辞, 按次数排。

    cap=3: 出现四次就算拐杖。两道筛:
      · 必须含物象/体感字(_CONCRETE) —— 「他没有说」这类高频语法串滤掉
      · 同族只留最长的 —— 「砂纸磨」「砂纸磨过」都高频时只报「砂纸磨过」
    """
    c = Counter()
    for t in texts:
        c.update(g for g in _grams(t) if any(ch in _CONCRETE for ch in g))
    # 人名、势力名要排掉 —— 「铁鹞子」(人名, 20 次)含「铁」字, 第一版混进了
    # 禁用表; 名字反复出现是正常的, 不是拐杖。
    nm = [x for x in names if x]
    hits = [(w, n) for w, n in c.most_common(400)
            if n > cap and not any(w in x or x in w for x in nm)]
    keep: List[Tuple[str, int]] = []
    for w, n in sorted(hits, key=lambda x: (-len(x[0]), -x[1])):
        if any(w in k for k, _ in keep):
            continue
        keep.append((w, n))
    keep.sort(key=lambda x: -x[1])
    return keep[:top]


def ban_block(texts: Iterable[str], cap: int = 3, top: int = 12,
              names: Iterable[str] = ()) -> str:
    """把已经用滥的措辞写成一段禁用指令。

    这一段是**程序算出来的**, 不是人手维护的词表 —— 每本书用滥的东西不一样,
    而且会随着写作长出来。写死一张表只能治当初见过的那几个。
    """
    bad = overused(texts, cap, top, names)
    if not bad:
        return ""
    return ("\n⛔ 【这些措辞本书已经用滥，这一章一个都不许再用】\n"
            + "、".join(f"{w}（{n}次）" for w, n in bad)
            + "\n　要写痛感/声音/质感，换**具体的生理细节**：牙龈发麻、"
              "指尖失去知觉、视野边缘发黑、听见自己的心跳盖过别的声音——"
              "不要现成套话。")


#: 正文里绝不该出现的**规划性词汇与章节回指**。
#: 这些是流水线泄漏，不是文笔问题：模型把「细纲」「第N章中」这类写作过程的
#: 词带进了成品。实测抓到一次: 第 12 章写着「第1章中，那块伴随他穿越的玉佩
#: ……不可逆的事实」—— 那是模型在**自己做一致性核对**，把核对过程当成叙述
#: 写了出来。而「不可逆的事实」正是我台账里的原词，是提示词教它这么想的。
META_LEAK = re.compile(
    r"细纲|大纲|本章要点|修订版|草稿|设定回顾|作者注|本章目标"
    r"|第\s*[0-9一二三四五六七八九十百]{1,4}\s*章中"
    r"|前文提到|前文已|上文提到|如前所述|不可逆的事实|已确立的")


def meta_leaks(text: str) -> List[str]:
    """正文里漏出来的规划性词汇/章节回指，返回命中的原句片段。"""
    out = []
    for m in META_LEAK.finditer(text or ""):
        a = max(0, m.start() - 12)
        out.append((text[a:m.end() + 16]).replace("\n", " "))
    return out[:5]

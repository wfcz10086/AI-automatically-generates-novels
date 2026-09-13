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
                "火烧冰锥磨刮搅绞碾锤砸筛糠蛇虫兽嘶吼腥锈甜臭麻痹胀裂"
                # 身体部位 —— 通读 18 章抓出「嘴角扯出/勾起…弧度」11 次,
                # 第一版物象表没有身体字, 整族漏掉
                "嘴唇眉眼喉咙掌拳颊肩膝弧瞳眸")

_STOP_RUN = re.compile(r"[^一-鿿]")


def _grams(text: str, lo: int = 3, hi: int = 5) -> Iterable[str]:
    for run in _STOP_RUN.split(text or ""):
        for n in range(lo, hi + 1):
            for i in range(len(run) - n + 1):
                yield run[i:i + n]


def overused(texts: Iterable[str], cap: int = 3, top: int = 20,
             names: Iterable[str] = ()) -> List[Tuple[str, int]]:
    texts = list(texts)
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
    nm = [x for x in names if x] + list(auto_names(texts))
    hits = [(w, n) for w, n in c.most_common(400)
            if n > cap and w[0] not in "的了是"
            and w[-1] not in "的了是"           # 「的尸体」「冰冷的」是切片边角
            and not any(w in x or x in w for x in nm)]
    keep: List[Tuple[str, int]] = []
    for w, n in sorted(hits, key=lambda x: (-len(x[0]), -x[1])):
        if any(w in k for k, _ in keep):
            continue
        keep.append((w, n))
    keep.sort(key=lambda x: -x[1])
    return keep[:top]


#: 地名的封闭后缀 —— 「狮子楼」出现 42 次是因为它是**地方**, 不是口头禅。
_PLACE_SUFFIX = set("楼县州府寺庄市巷街坊山河门桥村镇窟")


def auto_names(texts: Iterable[str]) -> set:
    """从正文里认出人名: 「X说/道/问/喝」这种说话归属出现 ≥3 次的就是名字。

    实测教训: 口头禅检测第一版把「潘金莲」(83 次)「鬼眼张」(66 次)当成
    口头禅报了出来 —— 名字高频是正常的。调用方给的 roster 不一定全
    (跑到中段新登场的角色不在开局名单里), 说话归属是正文自带的名字来源。
    """
    c = Counter()
    func = set("的了是在和与也就都而把被给这那")
    for t in texts:
        # 名字和说话动词之间常隔着状语:「孙雪娥**低声**道」「鬼眼张**压低声音**道」
        # 第一版要求紧邻, 漏掉了大半 —— 允许中间隔 0~4 个字。
        for m in re.finditer(
                r"([一-鿿]{2,3})[^，。！？\n」”]{0,4}?(?:说道|说|道|问|喝|冷笑|叹|开口)",
                t or ""):
            w = m.group(1)
            if any(ch in func for ch in w):
                continue
            c[w] += 1
    return {w for w, n in c.items() if n >= 3}


def catchphrases(texts: Iterable[str], cap: int = 12,
                 names: Iterable[str] = ()) -> List[Tuple[str, int]]:
    """口头禅超载: 3~6 字实词组出现 cap 次以上。

    通读 18 章抓出「这笔账/算得过来/这笔买卖」全文 23 次 —— 人设标签本身
    没错, 错在**连配角的内心戏都在算账**, 梗成了万能填充物。
    物象字过滤对它无效(「这笔账」没有物象字), 所以单开一路: 纯频次 + 
    实词占比 ≥ 2/3 + 人名排除。阈值 12 起报 —— 低了会把正常搭配拉进来。
    """
    texts = list(texts)
    func = set("的了是在和与也就都而但把被给从对向这那之其于及或并则已未"
               "不无有会能要很再又还只我你他她它们个为以所如若使让过")
    c = Counter()
    spread = Counter()          # 出现在几章里
    for t in texts:
        seen_here = set()
        for run in _STOP_RUN.split(t or ""):
            for n in range(3, 7):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if g[0] in "的了是" or g[-1] in "的了是":
                        continue          # 「的声音」「了一下」是切片边角
                    if sum(1 for ch in g if ch in func) * 3 > len(g):
                        continue
                    c[g] += 1
                    seen_here.add(g)
        spread.update(seen_here)
    nm = [x for x in names if x] + list(auto_names(texts))

    def _is_name(w: str) -> bool:
        # 「门庆的」「西门大官」这类带尾巴/截半的名字变体也要归到名下 ——
        # 去掉助词尾再比, 且 2 字以上重叠就算沾亲
        w2 = w.strip("的地得了着")
        return any(w2 and (w2 in x or x in w2) or
                   (len(w2) >= 2 and any(w2[i:i+2] in x
                                         for i in range(len(w2) - 1)))
                   for x in nm)

    # 分布形状是人名和口头禅最硬的区分: 口头禅是**习惯**, 全书均匀
    # (「这笔账」散在大半本书里); 人名跟着剧情段落走, 只聚在某几章
    # (赵铁柱只在沧州篇 8 章里出现)。roster 或说话归属漏掉的名字,
    # 这道筛还能兜住 —— 而且它不依赖任何词表。
    min_spread = max(3, len(texts) // 2)
    hits = [(w, k) for w, k in c.most_common(200)
            if k >= cap and w[-1] not in _PLACE_SUFFIX
            and spread[w] >= min_spread and not _is_name(w)]
    keep: List[Tuple[str, int]] = []
    for w, k in sorted(hits, key=lambda x: (-len(x[0]), -x[1])):
        if any(w in K for K, _ in keep):
            continue
        keep.append((w, k))
    keep.sort(key=lambda x: -x[1])
    return keep[:8]


def ban_block(texts: Iterable[str], cap: int = 3, top: int = 12,
              names: Iterable[str] = ()) -> str:
    """把已经用滥的措辞写成一段禁用指令。

    这一段是**程序算出来的**, 不是人手维护的词表 —— 每本书用滥的东西不一样,
    而且会随着写作长出来。写死一张表只能治当初见过的那几个。
    """
    texts = list(texts)
    bad = overused(texts, cap, top, names)
    mouth = catchphrases(texts, names=names)
    extra = ("\n　口头禅超载（连配角都在用，梗已成万能填充物，本章配额："
             "主角至多一次、配角零次）：" + "、".join(
                 f"{w}（{k}次）" for w, k in mouth)) if mouth else ""
    if not bad and not mouth:
        return ""
    if not bad:
        return "\n⛔ 【措辞纪律】" + extra
    return ("\n⛔ 【这些措辞本书已经用滥，这一章一个都不许再用】\n"
            + "、".join(f"{w}（{n}次）" for w, n in bad)
            + "\n　要写痛感/声音/质感，换**具体的生理细节**：牙龈发麻、"
              "指尖失去知觉、视野边缘发黑、听见自己的心跳盖过别的声音——"
              "不要现成套话。" + extra)


#: 正文里绝不该出现的**规划性词汇与章节回指**。
#: 这些是流水线泄漏，不是文笔问题：模型把「细纲」「第N章中」这类写作过程的
#: 词带进了成品。实测抓到一次: 第 12 章写着「第1章中，那块伴随他穿越的玉佩
#: ……不可逆的事实」—— 那是模型在**自己做一致性核对**，把核对过程当成叙述
#: 写了出来。而「不可逆的事实」正是我台账里的原词，是提示词教它这么想的。
#: 两类分开管:
#:   词汇类 —— 只在**叙述**里算泄漏。实测误报: 角色让人伪造文书时说
#:   「现在, 写个草稿」, 「草稿」是戏内的; 台词里说什么都是剧情, 先剥引号。
#:   回指类 ——「第N章中」「如前所述」在哪儿都是泄漏, 台词里出现更荒唐。
_LEAK_VOCAB = re.compile(
    r"细纲|大纲|本章要点|修订版|草稿|设定回顾|作者注|本章目标"
    r"|不可逆的事实|已确立的")
_LEAK_REF = re.compile(
    r"第\s*[0-9一二三四五六七八九十百]{1,4}\s*章中"
    r"|前文提到|前文已|上文提到|如前所述")
_QUOTED = re.compile(r"[「『\"“][^」』\"”]{0,200}[」』\"”]")

#: 旧名保留 —— 有测试/调用引用它
META_LEAK = re.compile(_LEAK_VOCAB.pattern + "|" + _LEAK_REF.pattern)


def meta_leaks(text: str) -> List[str]:
    """正文里漏出来的规划性词汇/章节回指，返回命中的原句片段。"""
    t = text or ""
    out = []
    narration = _QUOTED.sub(lambda m: "＂" * len(m.group(0)), t)   # 等长占位, 位置不变
    for pat, hay in ((_LEAK_VOCAB, narration), (_LEAK_REF, t)):
        for m in pat.finditer(hay):
            a = max(0, m.start() - 12)
            out.append((t[a:m.end() + 16]).replace("\n", " "))
    return out[:5]

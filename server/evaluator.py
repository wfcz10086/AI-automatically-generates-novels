"""质量评估器.

L1 机器指标 —— 移植自 x10086 skills/anti-ai-tell-audit, 并接入题材包的专属黑名单.
用途: ① 生成后自动判分, 不合格触发重写  ② 回归基准跑分
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Any, List

STRUCT = {
    "【】心理描写": (r"【[^】]+】", 2),
    "散文里的粗体": (r"\*\*[^*\n]+\*\*", 3),
    "分隔线": (r"^---\s*$", 0),
    "markdown表格": (r"^\s*\|.+\|", 0),
    "prompt残留": (r"\[(?:THINK|PLAN|需求|意图)[^\]]*\]", 0),
    "标题残留": (r"^#{1,6}\s", 0),
}

CLICHES_COMMON = ["总而言之", "综上所述", "值得一提的是", "不得不说", "必须指出", "毫无疑问",
                  "越来越多", "事实证明", "众所周知", "据悉", "大致可以分为", "不可忽视"]
# 精确词只能抓字面, 实测 30 章里"瞳孔一缩"只中 2 次, 而"瞳孔骤缩""瞳孔剧烈收缩"
# 合计 9 次全部绕过。所以套话检测必须用词根正则, 不能只做子串匹配。
CLICHE_PATTERNS = [
    (r"瞳孔(?:[一猛骤]?[缩紧])|瞳孔[^。，！？]{0,4}(?:收缩|骤缩|一缩)", "瞳孔X缩"),
    (r"(?:冷笑|嗤笑|嘲笑|讥笑)(?:了)?(?:一声)?(?=[，,。：:“\"、\s]|道|着)", "冷笑"),
    (r"嘴角(?:微微)?(?:勾起|上扬|扯出|噙着|挑起)", "嘴角勾起"),
    (r"缓缓(?:打开|起身|睁开|抬起|转过|站起|放下|开口|点头)", "缓缓X"),
    (r"深(?:深)?吸(?:了)?一口(?:凉)?气", "深吸一口气"),
    (r"眼底(?:寒光|冷光|精光)(?:一闪|闪过)", "眼底寒光一闪"),
    (r"[一-鿿]{0,2}嘴角(?:抽动|一抽)", "嘴角抽动"),
    (r"心(?:里|中|头)(?:猛地)?咯噔", "心里咯噔"),
    (r"(?:不敢|难以)置信", "不可置信"),
    (r"眼神(?:变得)?(?:冰冷|冷冽|骤冷)|眼神(?:陡然|骤然)[^。，]{0,3}冷", "眼神变冷"),
    (r"毛骨悚然|不寒而栗|汗毛倒竖|冷意(?:从|自)脚底", "惊悚套话"),
    (r"空气(?:仿佛|似乎|像是)?(?:瞬间)?(?:凝固|凝滞)", "空气凝固"),
    (r"脸色(?:瞬间)?(?:煞白|惨白|铁青|阴沉)", "脸色X白"),
    (r"(?:冷汗|汗珠)(?:涔涔|直流|密布|渗出)", "冷汗直流"),
    (r"[一-鿿]{0,3}(?:暗道|心中暗[骂想忖]|暗自思忖)", "暗道"),
    (r"平静得(?:像是)?(?:一潭)?死水|(?:像|如)一潭死水", "一潭死水"),
    # LLM 中文写作的招牌反转句 —— 人工鉴定 46 章确认为最重的 AI 指纹(6.0/万字)
    (r"不是[^。，]{2,12}，(?:而)?是[^。，]{2,14}[。！]", "「不是A是B」反转句"),
    # 历史文的新马甲: 换题材 AI 指纹会换装 —— 都市文的「空气里弥漫」灭了,
    # 文言腔比喻连接词「仿佛/宛如」在明代文里冒到 6.7/万字(都市书仅 1.1)
    (r"仿佛|宛如|犹如", "仿佛宛如"),
    (r"眼神[^。，]{0,6}(?:锐利|深邃|浑浊|复杂)|像两口枯井", "眼神形容"),
    (r"声音沙哑|嗓音沙哑", "声音沙哑"),
    (r"才刚刚开始|才(?:真正)?开始[。！]", "才刚刚开始"),
    (r"安静得(?:可怕|吓人)|死一般的(?:寂静|沉默)", "安静得可怕"),
    (r"摆出一副[^。，]{2,10}的(?:姿态|样子|架势)", "摆出一副X姿态"),
    (r"[一-鿿]{2}感(?=[。！])", "抽象X感收尾"),
]
CLICHES_NOVEL = ["不可置信", "毛骨悚然", "不寒而栗", "汗毛倒竖", "空气仿佛凝固"]

# 开局就该禁的默认口癖 —— 口癖统计要攒够 3 章才有数据, 而前 3 章恰恰最重要。
# 实测新稿第 1 章第 18 行就写了「瞳孔骤缩」, 就是这个冷启动缺口。
DEFAULT_TICS = [
    "瞳孔一缩", "瞳孔骤缩", "瞳孔剧烈收缩", "冷笑一声", "嘴角勾起", "嘴角微微上扬",
    "深吸一口气", "缓缓起身", "缓缓睁开", "缓缓打开", "心里咯噔", "不可置信",
    "脸色惨白如纸", "抖得像风中的落叶", "毛骨悚然", "空气仿佛凝固", "眼神变得冰冷",
    "如遭雷击", "浑身一震", "冷汗涔涔", "暗道不好", "似笑非笑地看着",
    # 跨书验证过的万金油(每本书都会自己长出来, 必须全局冷启动就禁)
    "才刚刚开始", "安静得可怕", "空气仿佛凝固", "心里跟明镜似的",
]
HOLLOW = ["非常", "十分", "特别", "极其", "格外", "尤其"]


# 现代概念 -> 古代对应说法。写历史/架空题材时直接抛现代词是硬伤,
# 但模型很难自觉替换, 需要机器检测出来。
MODERN_TERMS: Dict[str, str] = {
    "现金流": "银钱周转", "成本": "本钱", "利润": "出息／利钱", "投资": "出本／入股",
    "风险": "干系", "效率": "省工省时", "项目": "这桩事", "团队": "一班人手",
    "数据": "簿册／数目", "系统": "章程", "资本": "本金", "市场": "行市",
    "客户": "主顾", "供应链": "货源", "股权": "股份", "分红": "分利",
    "评估": "估量", "策略": "谋划", "资源": "人手财货", "流程": "章程步骤",
    "信息": "消息", "逻辑": "道理", "概念": "说法", "模式": "路数",
}


# 情绪/感官形容词 —— 单个无害, 密度高了就是「文学腔糊墙」
MOOD_ADJ = re.compile(
    r"冰冷|冷硬|昏黄|斑驳|粘稠|黏腻|刺耳|尖锐|凌乱|颤抖|苍白|灰败|深邃|锐利"
    r"|阴鸷|压抑|窒息|沙哑|干涩|潮湿|酸腐|惨白|刺鼻|浑浊|滚烫|冰凉|燥热")
ENV_OPEN = re.compile(r"灯|光|味|声|风|雨|雾|影|墙|窗|尘|空气|阳光|热浪|气息")


def cn_len(t: str) -> int:
    return len(re.findall(r"[一-鿿]", t))


def audit(text: str, extra_blacklist: List[str] | None = None,
          target_words: int = 0, check_modern: bool = False) -> Dict[str, Any]:
    """返回 {score, issues[], stats{}}  score 100 分制, 越高越好."""
    cn = cn_len(text)
    if cn < 100:
        return {"score": 0, "issues": [{"level": "high", "type": "长度不足",
                                        "detail": f"仅 {cn} 汉字"}], "stats": {"cn": cn}}

    issues: List[Dict[str, Any]] = []
    per_k = lambda n: n / (cn / 1000)  # 每千字频次

    # 1 结构性 AI tell
    for name, (pat, thr) in STRUCT.items():
        hits = re.findall(pat, text, flags=re.M)
        if len(hits) > thr:
            issues.append({"level": "high" if thr == 0 else "mid", "type": f"结构:{name}",
                           "count": len(hits), "threshold": thr, "samples": hits[:3]})

    # 2 通用套话 (字面匹配足够)
    hit = {w: text.count(w) for w in CLICHES_COMMON if text.count(w)}
    if hit and per_k(sum(hit.values())) > 1.0:
        issues.append({"level": "mid", "type": "通用套话", "count": sum(hit.values()),
                       "per_1k": round(per_k(sum(hit.values())), 2), "samples": list(hit)[:5]})

    # 3 小说套话 —— 词根正则, 抓变体
    pat_hits: Dict[str, int] = {}
    for pat, name in CLICHE_PATTERNS:
        c = len(re.findall(pat, text))
        if c:
            pat_hits[name] = c
    if pat_hits and per_k(sum(pat_hits.values())) > 1.5:
        issues.append({"level": "mid", "type": "小说套话", "count": sum(pat_hits.values()),
                       "per_1k": round(per_k(sum(pat_hits.values())), 2),
                       "samples": [f"{k}×{v}" for k, v in
                                   sorted(pat_hits.items(), key=lambda x: -x[1])[:5]]})

    # 3.3 环境铺陈开篇 —— 「地点+光线+气味」三件套是开局杀手(novel-common 红线第一条)
    head = text.strip()[:150]
    if ("“" not in head and '"' not in head and ENV_OPEN.search(head[:60])
            and len(re.findall(r"[一-鿿]", head)) > 60):
        issues.append({"level": "mid", "type": "环境铺陈开篇",
                       "detail": head[:50] + "…（应以对白/动作/事件开场）"})

    # 3.4 感官形容词密度
    mood = len(MOOD_ADJ.findall(text))
    if per_k(mood) > 2.5:
        issues.append({"level": "mid", "type": "情绪形容词过密",
                       "count": mood, "per_1k": round(per_k(mood), 1),
                       "samples": list(dict.fromkeys(MOOD_ADJ.findall(text)))[:6]})

    # 3.45 比喻密度
    simile = len(re.findall(r"像[^。，]{2,18}[。，]|如同|仿佛|宛如", text))
    if per_k(simile) > 1.8:
        issues.append({"level": "low", "type": "比喻过密",
                       "count": simile, "per_1k": round(per_k(simile), 1)})

    # 3.46 对白节奏切碎 —— AI 不敢让对白裸奔: 每两句必垫神态/动作打断。
    # 好作者常有 3-6 句连排干对白(连珠炮), 实测 AI 稿最长干对白串只有 2。
    _DRESS = re.compile(
        r"冷哼|嗤笑|皱(?:了皱)?眉|眯起|瞪|堆起|挤出|扯出|咬牙|叹(?:了口)?气"
        r"|摇(?:了摇)?头|点(?:了点)?头|摆手|压低声音|沉声|淡淡|轻声|冷冷|打量")
    paras_d = [x.strip() for x in text.split("\n") if x.strip()]
    runs, cur = [], 0
    for p_ in paras_d:
        if "“" in p_:
            outside = re.sub(r"“[^”]*”", "", p_)
            if len(re.findall(r"[一-鿿]", outside)) <= 8 and not _DRESS.search(outside):
                cur += 1
                continue
        if cur:
            runs.append(cur)
        cur = 0
    if cur:
        runs.append(cur)
    max_run = max(runs) if runs else 0
    if len([p_ for p_ in paras_d if "“" in p_]) >= 10 and max_run < 3:
        issues.append({"level": "mid", "type": "对白节奏切碎",
                       "detail": f"最长连续干对白仅 {max_run} 句——每两句就被神态/"
                                 f"动作打断，缺少连珠炮式交锋"})

    # 3.47 状态复述（写了情绪又解释一遍 = 废话）
    echo = len(re.findall(r"(?:慌|怕|怒|惊|喜)[^。]{0,20}。(?:他|她)?(?:知道|明白|清楚|心里)", text))
    if per_k(echo) > 0.8:
        issues.append({"level": "low", "type": "状态复述废话",
                       "count": echo, "per_1k": round(per_k(echo), 1)})

    # 3.48 三层连挂修饰（一个动作披挂三件描写）
    multi = len(re.findall(r"[，。][^，。]{2,10}一声[^，。]{0,14}，[^，。]{4,18}，[^，。]{4,18}[，。]", text))
    if multi >= 3:
        issues.append({"level": "low", "type": "动作修饰连挂",
                       "count": multi})

    # 3.49 成稿卫生 —— 这几类是「一眼就看出来的低级错」, 读者比任何指标都敏感,
    # 而框架此前一条都没检测。全是外部评审替我们抓到的。
    #
    # (a) 英文残留: 模型偶尔把「夫君」写成 husband、「折扣」写成 Discount。
    #     中文小说正文里不该有成串英文(专有名词如 CPU/DNA 另说, 但历史文里没有)。
    en = [w for w in re.findall(r"[A-Za-z]{3,}", text)
          if w.lower() not in ("cpu", "dna", "gdp", "app", "kpi", "ceo", "cto")]
    if en:
        issues.append({"level": "high", "type": "英文残留",
                       "count": len(en), "samples": list(dict.fromkeys(en))[:5]})

    # (b) 正文里的 markdown 标题: 章节正文是纯文本, 出现 # 标题就是格式漏出来了
    md_head = re.findall(r"^#{1,6}\s+\S.*$", text, re.M)
    if md_head:
        issues.append({"level": "mid", "type": "正文出现markdown标题",
                       "samples": [h[:24] for h in md_head[:3]]})

    # (c) 引号风格混用: 同一本书要么全用弯引号, 要么全用直角引号。
    #     实测第 20-21 章突然整章换成「」, 前 19 章都是 “”, 像从别处粘来的。
    n_curly, n_corner = text.count(chr(0x201C)), text.count(chr(0x300C))
    if n_curly and n_corner and min(n_curly, n_corner) > max(n_curly, n_corner) * 0.15:
        issues.append({"level": "low", "type": "引号风格混用",
                       "detail": f"弯引号 {n_curly} / 直角引号 {n_corner}"})

    # 3.5 现代词泄漏 (历史/架空题材)
    if check_modern:
        mod = {w: text.count(w) for w in MODERN_TERMS if w in text}
        if mod:
            issues.append({"level": "mid", "type": "现代词泄漏",
                           "count": sum(mod.values()),
                           "samples": [f"{w}→{MODERN_TERMS[w]}" for w in list(mod)[:5]]})

    # 4 题材专属黑名单 —— 出现即算失败
    for w in (extra_blacklist or []):
        if w and w in text:
            issues.append({"level": "high", "type": "题材黑名单", "samples": [w]})

    # 5 空洞形容词密度
    hollow = sum(text.count(w) for w in HOLLOW)
    if per_k(hollow) > 3:
        issues.append({"level": "low", "type": "空洞形容词", "count": hollow,
                       "per_1k": round(per_k(hollow), 2)})

    # 6 句式重复: 连续 3 段同开头
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    starts = [p[:2] for p in paras]
    for i in range(len(starts) - 2):
        if starts[i] and starts[i] == starts[i + 1] == starts[i + 2]:
            issues.append({"level": "mid", "type": "句式重复",
                           "samples": [f"连续3段以「{starts[i]}」开头"]})
            break

    # 7 对话占比
    # 引号用码点构造，避免源码里的中文弯引号被编辑器规范化成直引号
    _LQ, _RQ = chr(0x201C), chr(0x201D)
    _LB, _RB = chr(0x300C), chr(0x300D)
    _pat = f"[{_LQ}][^{_RQ}]*[{_RQ}]|[{_LB}][^{_RB}]*[{_RB}]"
    dialog = sum(len(m) for m in re.findall(_pat, text))
    ratio = dialog / max(1, len(text))
    if ratio < 0.15:
        issues.append({"level": "low", "type": "对话过少", "ratio": round(ratio, 3)})

    # 8 段落长度方差 (防全篇一个节奏)
    if len(paras) > 5:
        lens = [len(p) for p in paras]
        avg = sum(lens) / len(lens)
        var = sum((x - avg) ** 2 for x in lens) / len(lens)
        if var < 400:
            issues.append({"level": "low", "type": "段落节奏平均", "variance": round(var)})

    # 9 字数达标 —— 偏离要分级。原来一律 mid(扣 7 分), 于是只写了 47% 的章
    # 照样能拿 87 分, 重写闸门根本不触发。60 万字的书这样每章少写一半,
    # 最后只能靠硬凑章数补, 节奏全垮。
    if target_words:
        rate = cn / target_words
        if rate < 0.6 or rate > 1.6:
            issues.append({"level": "high", "type": "字数严重偏离",
                           "detail": f"{cn}/{target_words} = {rate:.0%}"})
        elif rate < 0.8 or rate > 1.4:
            issues.append({"level": "mid", "type": "字数偏离",
                           "detail": f"{cn}/{target_words} = {rate:.0%}"})

    weight = {"high": 15, "mid": 7, "low": 3}
    score = max(0, 100 - sum(weight[i["level"]] for i in issues))
    return {
        "score": score,
        "issues": issues,
        "stats": {"cn": cn, "paras": len(paras), "dialog_ratio": round(ratio, 3)},
    }




# ------------------------------------------------------------------ 全书体检
# 网文里最常被顺手抓来用的真实历史人物 —— 架空世界出现即穿帮
REAL_PEOPLE = ["赵构", "赵匡胤", "宋徽宗", "宋高宗", "蔡京", "高俅", "童贯", "秦桧",
               "岳飞", "韩世忠", "李清照", "苏轼", "王安石", "司马光", "包拯",
               "诸葛亮", "曹操", "魏征", "李世民", "武则天", "朱元璋", "康熙", "乾隆"]


def book_audit(chapters: Dict[int, str], *, characters: List[str] | None = None,
               forbidden_terms: List[str] | None = None,
               forbidden_people: List[str] | None = None,
               protagonist: str = "") -> Dict[str, Any]:
    """跨章体检 —— 单章合格不等于全书合格。

    实测教训: 逐章 AI 味均分 95.2, 但"嘴角勾起"在 30 章里出现 40 次。
    单章 1-2 次不触发阈值, 连起来看就是灾难。以下维度只有全书视角能发现。
    """
    if not chapters:
        return {"error": "没有章节"}
    order = sorted(chapters)
    allt = "\n".join(chapters[n] for n in order)
    total_cn = len(re.findall(r"[一-鿿]", allt))
    per_10k = lambda n: round(n / max(1, total_cn / 10000), 1)
    issues: List[Dict[str, Any]] = []

    # 1 套话的全书频次
    # 用密度而非绝对次数 —— 绝对次数不随篇幅缩放, 54 万字的书必然次次报「泛滥」,
    # 而「冷笑」119 次 / 54 万字 = 2.2 每万字, 密度其实正常。
    tics = []
    for pat, name in CLICHE_PATTERNS:
        c = len(re.findall(pat, allt))
        d = per_10k(c)
        if c >= 8 and d >= 1.5:
            tics.append({"tic": name, "count": c, "per_10k": d})
    tics.sort(key=lambda x: -x["per_10k"])
    if tics:
        worst = tics[0]["per_10k"]
        issues.append({"level": "high" if worst >= 4 else "mid" if worst >= 2.5 else "low",
                       "type": "口癖泛滥", "detail": tics[:8]})

    # 2 世界观术语冲突 (架空却用真朝代名之类)
    forb = {}
    for t in (forbidden_terms or []):
        c = allt.count(t)
        if c:
            forb[t] = c
    if forb:
        issues.append({"level": "high", "type": "禁用术语出现",
                       "detail": dict(sorted(forb.items(), key=lambda x: -x[1]))})

    # 2.5 架空世界里的真实历史人物 (穿帮)
    if forbidden_people:
        pp = {n: allt.count(n) for n in forbidden_people if n in allt}
        if pp:
            issues.append({"level": "high", "type": "架空世界出现真实历史人物",
                           "detail": dict(sorted(pp.items(), key=lambda x: -x[1]))})

    # 3 角色出场分布 —— 主角垄断 / 配角纸片人
    dist = []
    for name in (characters or []):
        chs = [n for n in order if name in chapters[n]]
        dist.append({"name": name, "count": allt.count(name),
                     "chapters": len(chs), "coverage": round(len(chs) / len(order), 2),
                     "first": chs[0] if chs else None})
    dist.sort(key=lambda x: -x["count"])
    if dist:
        top = dist[0]
        rest = sum(d["count"] for d in dist[1:]) or 1
        ratio = top["count"] / rest
        if ratio > 1.2:
            issues.append({"level": "mid", "type": "主角戏份垄断",
                           "detail": f"{top['name']} {top['count']} 次 vs 其余全部 {rest} 次 "
                                     f"(1:{rest/top['count']:.2f})"})
        ghosts = [d["name"] for d in dist if d["chapters"] and d["coverage"] < 0.15]
        if ghosts:
            issues.append({"level": "mid", "type": "配角纸片化",
                           "detail": f"出场率低于 15% 的角色: {'、'.join(ghosts[:10])}"})
        never = [d["name"] for d in dist if not d["chapters"]]
        if never:
            issues.append({"level": "low", "type": "角色档案未落地",
                           "detail": f"档案里有但正文从未出现: {'、'.join(never[:10])}"})

    # 3.9 引号风格跨章不统一 —— 单章内不混用也可能整章换风格。
    # 实测第 20-21 章整章用「」, 前 19 章全是 “”, 读者一翻就出戏。
    styles = {}
    for n, t in chapters.items():
        c, k = t.count(chr(0x201C)), t.count(chr(0x300C))
        if max(c, k) >= 5:
            styles[n] = "curly" if c > k else "corner"
    if len(set(styles.values())) > 1:
        from collections import Counter as _C
        tally = _C(styles.values())
        major, _ = tally.most_common(1)[0]
        odd = sorted(n for n, v in styles.items() if v != major)
        issues.append({"level": "mid", "type": "引号风格不统一",
                       "detail": f"全书主用{'弯引号' if major == 'curly' else '直角引号'}，"
                                 f"这些章不一样：{odd[:8]}"})

    # 4 地名/主场漂移
    # 只统计行政区; 「西门府/王府」这类宅邸不算主场, 否则必然误报漂移
    # 地名前缀不能硬取两字: 「孟州」只有一个前缀字, [一-鿿]{2} 会把它匹配成
    # 「在孟州」「去孟州」「回孟州」三个不同地名, 于是同一个地方自己跟自己抢主场。
    # 先剥掉动词/介词前缀, 再统计。
    _LEAD = re.compile(r"^(?:在|去|回|到|往|自|离|赴|从|经|至|入|出|抵|返)")
    raw_places = re.findall(r"([一-鿿]{1,3}(?:县|州|府|镇|村|城))", allt)
    places = Counter()
    for w in raw_places:
        w = _LEAD.sub("", w)
        if len(w) < 2:
            continue
        if re.search(r"(?:府|宅|邸)$", w) and not w.endswith(("州府", "京府")) \
                and w[-1] not in "县镇村城":
            continue
        places[w] += 1
    # 出现在大多数章节里的地名是「城市底色」(如书中城市名), 不参与主场竞争判定
    ch_count = len(chapters)
    def coverage(w):
        return sum(1 for t in chapters.values() if w in t) / max(1, ch_count)
    contenders = [(w, c) for w, c in places.most_common(6) if coverage(w) < 0.6]
    main = contenders[:3]
    if len(main) >= 2 and main[1][1] > main[0][1] * 0.4:
        issues.append({"level": "mid", "type": "主场地点漂移",
                       "detail": f"{main[0][0]} {main[0][1]} 次 vs {main[1][0]} {main[1][1]} 次，"
                                 f"两地都像主场，读者会混乱"})

    # 5 开篇句式重复
    heads = Counter((chapters[n].strip().split("\n")[0][:6]) for n in order)
    dup = [f"{k}×{v}" for k, v in heads.most_common(5) if v > 1]
    if dup:
        issues.append({"level": "low", "type": "章节开篇雷同", "detail": dup})

    # 6 章节字数波动
    lens = [len(re.findall(r"[一-鿿]", chapters[n])) for n in order]
    avg = sum(lens) / len(lens)
    outliers = [(order[i], l) for i, l in enumerate(lens) if l < avg * 0.6 or l > avg * 1.6]
    if outliers:
        issues.append({"level": "low", "type": "章节长度失衡",
                       "detail": f"均值 {avg:.0f} 字，偏离过大: " +
                                 "、".join(f"第{n}章{l}字" for n, l in outliers[:6])})

    weight = {"high": 18, "mid": 9, "low": 4}
    score = max(0, 100 - sum(weight[i["level"]] for i in issues))
    return {"score": score, "chapters": len(order), "total_words": total_cn,
            "issues": issues, "tics": tics[:12], "cast": dist[:20]}


# ------------------------------------------------------------------ 窗口体检
def _shingles(text: str, n: int = 5) -> set:
    """字符 n-gram 集合, 用来算相邻章节的重复度."""
    t = re.sub(r"\s+", "", text)
    return {t[i:i + n] for i in range(0, max(0, len(t) - n), 2)}


def window_audit(chapters: Dict[int, str], center: int, span: int = 3,
                 outlines: Dict[str, str] | None = None) -> Dict[str, Any]:
    """滑动窗口体检 —— 拿本章和前后几章贴在一起看。

    单章体检看不出的问题:
      * 和上一章开场方式雷同
      * 同一个桥段/比喻反复用
      * 剧情原地踏步 (三章都在同一场冲突里绕)
      * 配角突然消失或突然冒出来
      * 口癖在窗口内堆积 (每章 2 次不超标, 连着 4 章就是 8 次)
    """
    order = sorted(chapters)
    if center not in chapters:
        return {"error": f"第 {center} 章不存在"}
    lo = [n for n in order if center - span <= n < center]
    win = lo + [center]
    cur = chapters[center]
    issues: List[Dict[str, Any]] = []

    # 1 与前几章的文本重复度
    cs = _shingles(cur)
    dups = []
    for n in lo:
        ps = _shingles(chapters[n])
        if not ps or not cs:
            continue
        j = len(cs & ps) / len(cs | ps)
        dups.append({"vs": n, "overlap": round(j, 3)})
        if j > 0.16:
            issues.append({"level": "high" if j > 0.24 else "mid", "type": "与邻章重复度过高",
                           "detail": f"与第 {n} 章 {j:.1%} 重合，疑似换汤不换药"})

    # 2 开场雷同
    heads = {n: re.sub(r"\s+", "", chapters[n])[:24] for n in win}
    ch = heads[center]
    for n in lo:
        same = len(set(ch[:12]) & set(heads[n][:12])) / 12
        if heads[n][:8] == ch[:8] or same > 0.75:
            issues.append({"level": "mid", "type": "开场与邻章雷同",
                           "detail": f"第 {center} 章开场与第 {n} 章高度相似"})
            break

    # 3 口癖在窗口内堆积
    wt = "\n".join(chapters[n] for n in win)
    tics = []
    for pat, name in CLICHE_PATTERNS:
        c = len(re.findall(pat, wt))
        if c >= 4:
            tics.append({"tic": name, "count": c, "span": len(win)})
    tics.sort(key=lambda x: -x["count"])
    if tics:
        issues.append({"level": "mid", "type": "窗口内口癖堆积",
                       "detail": [f"{t['tic']}×{t['count']}（近{t['span']}章）" for t in tics[:6]]})

    # 4 剧情原地踏步: 窗口内高频实体几乎不变
    def keyset(t):
        return set(w for w, c in Counter(re.findall(r"[一-鿿]{2,4}", t)).most_common(40))
    if lo:
        prev_keys = set().union(*(keyset(chapters[n]) for n in lo))
        cur_keys = keyset(cur)
        fresh = cur_keys - prev_keys
        if len(fresh) / max(1, len(cur_keys)) < 0.25:
            issues.append({"level": "mid", "type": "剧情疑似原地踏步",
                           "detail": f"本章新元素仅占 {len(fresh)/max(1,len(cur_keys)):.0%}，"
                                     f"与前 {len(lo)} 章高度同质"})

    # 5 角色出场连续性
    if outlines:
        cast_cur = set(re.findall(r"[一-鿿]{2,4}", outlines.get(str(center), "")))
        vanished = []
        for n in lo:
            for name in re.findall(r"[一-鿿]{2,4}", outlines.get(str(n), "")):
                if name in chapters[n] and name not in cur and len(name) >= 2:
                    vanished.append(name)
        vanished = [v for v, c in Counter(vanished).items() if c >= len(lo)]
        if vanished:
            issues.append({"level": "low", "type": "角色断线",
                           "detail": f"连续出现于前 {len(lo)} 章、本章突然消失: "
                                     + "、".join(vanished[:6])})

    weight = {"high": 16, "mid": 8, "low": 3}
    return {"center": center, "window": win, "score": max(0, 100 - sum(
        weight[i["level"]] for i in issues)), "issues": issues, "overlaps": dups}

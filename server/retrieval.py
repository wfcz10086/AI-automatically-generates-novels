"""统一检索层 —— 内部记忆 + 外部搜索合并，写作时自动触发。

设计要点: 搜索不是一个"先去生成考据卡"的独立步骤, 而是 L4 召回层的一半。
写每一章时:
    ① 从本章细纲里识别出"需要事实支撑"的点 (物价 / 官职 / 年份 / 器物 / 度量衡)
    ② 内部先查 (FTS5): 这本书之前是否已经写过、已经查过
    ③ 内部查不到才走外部 (SearXNG), 结果落盘缓存并写回内部记忆
    ④ 两路结果合并成 L4 注入
这样同一个知识点全书只查一次, 之后都从内部记忆命中, 且前后写法自动一致。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .registry import registry

# 需要事实支撑的信号: 出现这些模式说明本章要写具体的、可以写错的东西
# 每项: (正则, 主题). 正则只捕获关键词本身, 不带上下文 —— 否则检索式会被噪声污染。
FACT_TRIGGERS: List[tuple] = [
    (r"(\d+\s*(?:两|贯|文|石|斗|匹|亩))", "度量衡与物价"),
    (r"(知县|知府|通判|提辖|都头|押司|县令|太师|御史|员外郎|转运使)", "官职与品级"),
    (r"(流放|刺配|杖刑|绞刑|斩首|徒刑|讼状|仵作|尸格)", "律法与量刑"),
    (r"(\d{3,4})\s*年", "年份与纪事"),
    (r"(科举|乡试|会试|殿试|童生|秀才|举人|进士)", "科举制度"),
    (r"(火药|造纸|活字印刷|水泥|玻璃|肥皂|蒸馏|织机|曲辕犁|漕运)", "工艺与器物"),
    (r"(厢军|禁军|团练|保甲|马军|步军|弓手)", "军制"),
    (r"(交子|会子|盐引|茶引|度牒|当铺|钱庄)", "货币与金融"),
]


def detect_fact_needs(text: str, era: str, limit: int = 4) -> List[Dict[str, str]]:
    """从章节细纲里识别需要查证的点, 生成检索式。"""
    seen, out = set(), []
    for pat, topic in FACT_TRIGGERS:
        for m in re.findall(pat, text or ""):
            key = topic
            if key in seen:
                continue
            seen.add(key)
            kw = str(m).strip()
            out.append({"topic": topic, "hint": kw,
                        "query": f"{era} {kw} {topic}".strip()})
            if len(out) >= limit:
                return out
    return out


class Retriever:
    """内部 FTS5 + 外部 SearXNG 的统一入口。"""

    STAGE_TOPICS: Dict[str, List[str]]

    def __init__(self, memory, project_dir: Path, era: str = "",
                 enable_web: bool = True, endpoint: Optional[str] = None,
                 summarize: Optional[Callable[[str], str]] = None,
                 topics: Optional[Dict[str, List[str]]] = None,
                 plan: Optional[Callable[[str], str]] = None):
        self.topics = topics or {}
        self.plan = plan                      # 让大模型决定"查什么"
        self.mem = memory
        self.dir = project_dir
        self.era = era
        self.enable_web = enable_web
        self.summarize = summarize
        self.sx = (registry.searcher(endpoint).bind_cache(project_dir / "research")
                   if enable_web else None)
        self.facts_path = project_dir / "facts.json"
        self.facts: Dict[str, Any] = {}
        if self.facts_path.exists():
            try:
                self.facts = json.loads(self.facts_path.read_text(encoding="utf-8"))
            except Exception:
                self.facts = {}

    # ---------------- 事实卡 ----------------
    _last_raw = ""
    _last_urls: List[str] = []

    def _save(self):
        self.facts_path.write_text(json.dumps(self.facts, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    def fact_for_verbose(self, need: Dict[str, str],
                         keep_raw: bool = False) -> Dict[str, Any]:
        """带失败原因的检索。

        「没检索到资料」有两种截然不同的情况: 搜索一条都没搜到, 和搜到了但守门
        判定答非所问。前者该换关键词, 后者该由用户决定要不要留原始摘录 ——
        接口只回一句「换个说法试试」的话, 用户根本不知道该往哪个方向使劲。
        """
        topic = need["topic"]
        before = dict(self.facts.get(topic) or {})
        card = self.fact_for(need)
        if card and card.get("card"):
            return {"ok": True, "card": card}
        rec = self.facts.get(topic) or {}
        if rec.get("rejected"):
            out = {"ok": False, "reason": "rejected",
                   "message": "搜到了资料，但守门判定答非所问（多为百科泛述或广告）",
                   "query": need.get("query", "")}
            if keep_raw and self._last_raw:
                self.facts[topic] = {"topic": topic, "card": self._last_raw[:800],
                                     "sources": self._last_urls[:3],
                                     "kept_raw": True,
                                     "built_at": time.strftime("%F %T")}
                self._save()
                return {"ok": True, "card": self.facts[topic], "raw": True}
            out["raw_preview"] = self._last_raw[:600]
            return out
        return {"ok": False, "reason": "no_hits",
                "message": "这个关键词一条都没搜到，换个说法或换个角度",
                "query": need.get("query", "")}

    def fact_for(self, need: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """一个知识点: 先查已建立的事实卡, 没有才联网, 查完写回记忆索引。"""
        topic = need["topic"]
        if topic in self.facts:
            return self.facts[topic]
        if not (self.enable_web and self.sx and self.sx.available()):
            return None

        hits = self.sx.search(need["query"], k=4)
        if not hits:
            self.facts[topic] = {"topic": topic, "card": "", "sources": [],
                                 "built_at": time.strftime("%F %T")}
            self._save()
            return None

        raw = "\n".join(f"- {h['title']}：{h['content'][:300]}" for h in hits)
        self._last_raw = raw
        self._last_urls = [h["url"] for h in hits]
        card = raw[:800]
        if self.summarize:
            got = self.summarize(
                f"下面是检索「{self.era} {topic}」得到的结果。\n"
                f"先判断这些结果是否真的回答了「{topic}」这个问题。\n"
                f"- 如果**没有**（结果是无关的百科泛述、广告、或答非所问），"
                f"只回复两个字：无\n"
                f"- 如果有，压成 5 条以内的写作硬事实，每条一句话带具体数字；"
                f"互相矛盾的标「存疑」；不确定的不要写\n"
                f"直接输出，无前言。\n\n{raw[:4000]}") or ""
            got = got.strip()
            # 无关就不入库 —— 垃圾卡片既占预算又误导
            if got in ("无", "", "None") or "无法生成" in got or "未包含" in got:
                self.facts[topic] = {"topic": topic, "card": "", "sources": [],
                                     "rejected": True, "built_at": time.strftime("%F %T")}
                self._save()
                return None
            card = got

        rec = {"topic": topic, "card": card,
               "sources": [h["url"] for h in hits[:3]],
               "built_at": time.strftime("%F %T")}
        self.facts[topic] = rec
        self._save()
        # 写回内部记忆 —— 下次同类问题直接内部命中, 不再联网
        self.mem.add("fact", f"fact-{topic}", f"考据·{topic}", card)
        return rec

    # ---------------- 由大模型决定查什么 ----------------
    def query_for_topic(self, topic: str, era: str = "") -> str:
        """把用户点名的主题变成真正搜得到东西的关键词。

        「明代镖局规矩」直接丢进搜索引擎是零结果 —— 用户描述的是「我想知道什么」,
        搜索需要的是「网页上会怎么写」。这一步同样交给模型, 和自动检索时
        plan_queries 的道理一样。
        """
        if not self.plan:
            return topic
        try:
            r = self.plan(
                f"把下面这个资料需求改写成 1 条能在中文搜索引擎里搜到结果的关键词。\n"
                f"需求：{topic}\n"
                f"{'时代背景：' + era if era else ''}\n\n"
                f"要求：用空格分隔的实词，去掉「规矩」「情况」「怎么样」这类虚词，"
                f"必要时补上朝代/行业/年份等限定。只输出关键词本身，不要引号和解释。")
            q = (r or "").strip().splitlines()[0].strip(' "「」')
            return q[:60] or topic
        except Exception:
            return topic

    def plan_queries(self, *, stage: str, context: str, k: int = 5,
                     hints: Optional[List[str]] = None) -> List[Dict[str, str]]:
        """让大模型读完设定/细纲后, 自己回答"这段要查证什么"。

        正则触发词只认得「知县」「交子」这类历史词, 遇到"XP漏洞卖给微软换 40 万美金"
        "养生茶注册非遗""LPL 春季赛 BP 规则"就完全抓不到。判断该查什么本身就是
        个理解任务, 应该交给模型。
        """
        if not self.plan:
            return []
        hint_line = ("参考方向（可以不用）：" + "、".join(hints[:8])) if hints else ""
        stage_desc = {"world": "构建世界观/时代背景", "cast": "设计人物与关系表",
                      "plot": "设计章节剧情", "chapter": "写本章正文",
                      "drive": "找能推动剧情的真实素材"}.get(stage, stage)
        # drive 阶段问的不是「写错了没有」, 而是「接下来能写什么」——
        # 前者查证对错(器物称谓物价), 后者找虚构不出来的真实做法当剧情素材。
        if stage == "drive":
            ask_line = ("请找出**能拿来当剧情素材**的真实内容：这个时代／这一行"
                        "真实发生过什么事、行内人真实的做局与自保手法、"
                        "制度上真实存在的漏洞、同类冲突在历史上怎么收场。\n"
                        "目的不是查证对错，是**找到虚构不出来的具体做法**塞进剧情。\n"
                        "越具体越好：具体的手法、具体的数目、具体的后果。\n\n")
        else:
            ask_line = ("请判断：写这部分时，哪些**具体的、写错了读者会发现**的"
                        "事实需要查证？\n"
                        "只挑真正需要外部资料的（真实的年份事件、行业数据、专业术语、"
                        "制度规则、器物工艺、地理常识、法律法规、产品与公司…）。\n"
                        "纯虚构设定（自创的功法、自创的国号、人物性格）不需要查，"
                        "不要列。\n\n")
        prompt = (
            f"你在帮一位网文作者做资料准备。当前任务：{stage_desc}。\n\n"
            f"下面是已有的设定与内容：\n{context[:3500]}\n\n"
            f"{hint_line}\n\n"
            f"{ask_line}"
            f"输出最多 {k} 条，每行一条，严格格式：\n"
            f"主题|检索式\n"
            f"（主题是 4-10 字的归类名，检索式是能直接丢进搜索引擎的一句话）\n"
            f"如果确实没有需要查的，只输出一行：无\n"
            f"直接输出，无前言。")
        try:
            raw = self.plan(prompt) or ""
        except Exception as e:
            print(f"[retrieval] 检索式规划失败, 降级正则: {e}")
            return []
        out: List[Dict[str, str]] = []
        for line in raw.splitlines():
            line = line.strip().lstrip("-*0123456789. ")
            if not line or line == "无":
                continue
            if "|" in line or "｜" in line:
                topic, q = re.split(r"[|｜]", line, 1)
            else:
                topic, q = line[:10], line
            topic, q = topic.strip()[:20], q.strip()[:80]
            if topic and q and len(q) > 3:
                out.append({"topic": topic, "hint": topic, "query": q})
            if len(out) >= k:
                break
        return out

    # ---------------- 主题落地 ----------------
    # 各创作阶段该查什么 —— 让世界观/人物/剧情都长在真实背景上
    # 各阶段该查什么由题材包 research_topics 决定 —— 都市金融该查股市融资,
    # 电竞该查赛事规则, 写死成历史题材是错的。这里只是兜底。
    STAGE_TOPICS: Dict[str, List[str]] = {
        "world": ["时代背景与社会风貌", "经济与物价", "制度与结构"],
        "cast":  ["称谓与身份", "典型职业"],
        "plot":  ["日常器物", "出行与通讯", "风俗礼仪"],
        # drive 是另一类用途: 前三种查的是「写得对不对」(器物称谓物价),
        # 这一种查的是「接下来能写什么」—— 真实发生过的事件、真实的做局手法、
        # 真实的制度漏洞, 拿来当剧情素材。虚构不出来的东西, 现实里有现成的。
        "drive": ["同期真实事件与时间线", "这一行的真实做法与门道",
                  "制度漏洞与钻空子的真实案例", "同类冲突在历史上怎么收场"],
    }

    def ground(self, stage: str, extra: Optional[List[str]] = None,
               per_topic: int = 3, context: str = "") -> str:
        """给某个创作阶段做背景落地: 批量查该阶段该知道的常识, 压成一块可注入文本。

        结果按主题缓存进 facts.json 与记忆索引, 全书只查一次, 后续阶段直接复用。
        """
        # research_topics 有两种写法, 都要吃得下:
        #   dict —— 按阶段分（{"world": [...], "cast": [...]}）
        #   list —— 不分阶段, 各阶段共用（新写的题材包常用这种简写）
        # 原来只认 dict, 遇到 list 直接 AttributeError, 整个背景落地被跳过 ——
        # 报的还是「'list' object has no attribute 'get'」这种看不出所以然的错。
        t = self.topics
        if isinstance(t, list):
            own = list(t)
        elif isinstance(t, dict):
            own = list(t.get(stage) or [])
        else:
            own = []
        hints = own or self.STAGE_TOPICS.get(stage, [])
        needs = self.plan_queries(stage=stage, context=context or "", hints=hints,
                                  k=per_topic + 2) if context else []
        if not needs:                       # 模型没给或不可用 -> 回退到题材包主题清单
            needs = [{"topic": t, "hint": t, "query": f"{self.era} {t}"}
                     for t in hints]
        needs += [{"topic": e, "hint": e, "query": f"{self.era} {e}"} for e in (extra or [])]

        blocks: List[str] = []
        for nd in needs:
            rec = self.facts.get(nd["topic"]) or self.fact_for(nd) or {}
            card = rec.get("card")
            if card:
                blocks.append(f"【{nd['topic']}】{card.strip()}")
        return "\n\n".join(blocks)

    # ---------------- 统一召回 ----------------
    def recall(self, chapter_outline: str, k: int = 6) -> Dict[str, Any]:
        """返回 {items, internal, external, needs} —— 供 L4 层直接使用。"""
        internal = self.mem.search(chapter_outline, k=k)
        needs = self.plan_queries(stage="chapter", context=chapter_outline, k=4)
        if not needs:
            needs = detect_fact_needs(chapter_outline, self.era)
        external: List[Dict[str, Any]] = []
        for nd in needs:
            rec = self.fact_for(nd)
            if rec and rec.get("card"):
                external.append({"kind": "fact", "title": f"考据·{rec['topic']}",
                                 "text": rec["card"], "sources": rec.get("sources", [])})
        # 内部命中里已有的 fact 条目去重
        seen = {e["title"] for e in external}
        items = external + [h for h in internal if h.get("title") not in seen]
        return {"items": items[:k + len(external)], "internal": len(internal),
                "external": len(external), "needs": [n["topic"] for n in needs]}

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
        # 缓存与计数由 registry 统一绑好（仓库级 .cache/search，跨书共享）——
        # 按次计费的源下，「同一个宋代盐引怎么走」不该因为换本书就再花一次额度。
        self.sx = registry.searcher(endpoint) if enable_web else None
        # 仓库级考据卡库：原始搜索结果本来就跨书共享（.cache/search），
        # 可「逐条判定 + 摘成卡片」那两次模型调用每本书都重跑了一遍 ——
        # 而那才是贵的部分。实测「宋代仵作验尸制度」被四本书各查一遍、
        # 「盐引制度」三遍。卡片按「时代 + 归一化主题」入库，跨书复用。
        self.shared_path = (Path(__file__).resolve().parents[1]
                            / ".cache" / "facts.json")
        self.shared: Dict[str, Any] = {}
        try:
            if self.shared_path.exists():
                self.shared = json.loads(self.shared_path.read_text(encoding="utf-8"))
        except Exception:
            self.shared = {}
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

    # ---------------- 主题归一 ----------------
    #: 归一化时丢掉的虚词与标点。模型每次换个说法（「宋代货币购买力与贯」/
    #: 「宋代钱两贯的换算与购买力」/「宋代货币购买力 贯 网文换算」）就绕过缓存，
    #: 实测一本书 387 次检索里有近百次是在重复查同一件事。
    _NOISE = re.compile(r"[\s·、,，/|｜:：的与和及在为对了个]+")

    @classmethod
    def _key(cls, topic: str) -> str:
        return cls._NOISE.sub("", str(topic or ""))[:24]

    def _by_key(self) -> Dict[str, str]:
        return {self._key(k): k for k in self.facts}

    def _shared_key(self, topic: str) -> str:
        """跨书键：时代 + 归一化主题。

        时代必须进键 —— 「盐引制度」在北宋和明代不是一回事，
        混用会把明代的卡片喂给宋代的书。
        """
        return f"{self._key(self.era)}|{self._key(topic)}"

    def _save_shared(self) -> None:
        try:
            self.shared_path.parent.mkdir(parents=True, exist_ok=True)
            self.shared_path.write_text(
                json.dumps(self.shared, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---------------- 结果过滤: 一条一条交给模型 ----------------
    _TAG = re.compile(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>")
    _ANY_TAG = re.compile(r"(?s)<[^>]+>")

    def _page_text(self, url: str, cap: int = 6000) -> str:
        """抓页面正文 —— **默认不用**，留给确实需要读长文的调用方。

        事实卡链路只吃检索源自带的 summary（见 _build_card 的说明）。
        """
        try:
            import requests
            r = requests.get(url, timeout=12, headers={
                "User-Agent": "Mozilla/5.0 (compatible; novelbot/1.0)"})
            r.encoding = r.apparent_encoding or r.encoding
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                return ""
            h = self._TAG.sub(" ", r.text)
            txt = self._ANY_TAG.sub(" ", h)
            txt = re.sub(r"&[a-z]{2,8};", " ", txt)
            txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
            txt = re.sub(r"\n\s*\n+", "\n", txt)
            return txt.strip()[:cap]
        except Exception:
            return ""

    def _judge(self, topic: str, hits: List[Dict[str, Any]]) -> List[int]:
        """逐条判定哪些结果真的回答了这个问题。

        原来是把 4 条片段一股脑拼起来让模型压成卡片 —— 垃圾把好料稀释掉，
        模型只能在噪声里挑，挑不出来就整条弃掉（实测拒绝率 53%）。
        改成先一条一条过筛，只把留下的送去摘要。
        """
        if not hits:
            return []
        if not self.summarize:
            return list(range(len(hits)))
        listing = "\n".join(
            f"{i+1}. [{h.get('engine','')}] {h.get('title','')[:70]}\n"
            f"   {h.get('url','')[:90]}\n   {(h.get('content') or '')[:220]}"
            for i, h in enumerate(hits))
        ask = (f"我要查的是：**{self.era} {topic}**\n\n"
               f"下面是搜索引擎返回的结果，逐条判断它**是不是真的在讲这件事**。\n"
               f"以下一律判为无关：搜索引擎首页、导航站、短视频/社交平台、"
               f"电商与广告、与主题无关的百科泛述（如查「市舶司公凭」却返回"
               f"「宋朝历史简介」）、正文为空。\n"
               f"宁可少留，不要留错 —— 留错一条，整张事实卡就被污染。\n\n"
               f"{listing}\n\n"
               f"只输出有关的编号，逗号分隔，如：1,4。一条都没有就输出：无")
        try:
            out = (self.summarize(ask) or "").strip()
        except Exception:
            return list(range(len(hits)))
        if "无" in out and not re.search(r"\d", out):
            return []
        return [int(x) - 1 for x in re.findall(r"\d+", out)
                if 0 <= int(x) - 1 < len(hits)][:5]

    def _reformulate(self, topic: str, tried: List[str]) -> str:
        """换个角度重写检索式。

        一发不中就把主题永久标记为「无关」，是原来最大的浪费 ——
        「北宋买扑盐引制度」「宋代市舶司公凭」这些明明查得到的题目，
        就因为第一条式子没中，全书再也不会去查第二次。
        """
        if not self.plan:
            return ""
        ask = (f"我要查的是：**{self.era} {topic}**\n"
               f"下面这些检索式都没查到有用的资料：\n"
               + "\n".join(f"- {q}" for q in tried[-4:]) +
               f"\n\n换一个角度重写检索式。可以：换更常见的说法、"
               f"换成学术/史料里的正式名称、拆成更小的子问题、"
               f"去掉限定词只留核心名词、或改查这件事所属的更大类目。\n"
               f"只输出一行新的检索式，不要解释。")
        try:
            q = (self.plan(ask) or "").strip().splitlines()[0]
        except Exception:
            return ""
        q = q.strip().strip("「」\"'` ")[:80]
        return q if q and q not in tried and len(q) > 3 else ""

    def fact_for(self, need: Dict[str, str], rounds: int = 3) -> Optional[Dict[str, Any]]:
        """一个知识点: 内部先查, 联网重试, 逐条过滤, 抓正文再摘, 落盘复用。"""
        topic = need["topic"]
        if topic in self.facts and self.facts[topic].get("card"):
            return self.facts[topic]
        # 同义主题已经查过就直接复用, 不再重复联网
        twin = self._by_key().get(self._key(topic))
        if twin and twin != topic and (self.facts[twin] or {}).get("card"):
            self.facts[topic] = dict(self.facts[twin], alias_of=twin)
            self._save()
            return self.facts[topic]
        # 别的书查过同一个时代的同一件事就直接用，省掉判定与摘要两次调用
        sk = self._shared_key(topic)
        hit = self.shared.get(sk)
        if hit and hit.get("card"):
            rec = dict(hit, topic=topic, from_shared=True)
            self.facts[topic] = rec
            self._save()
            try:
                self.mem.add("fact", f"fact-{topic}", f"考据·{topic}", rec["card"])
            except Exception:
                pass
            return rec
        if not (self.enable_web and self.sx and self.sx.available()):
            return None

        prev = self.facts.get(topic) or {}
        tried: List[str] = list(prev.get("tried") or [])
        q = need.get("query") or f"{self.era} {topic}"
        for rnd in range(max(1, rounds)):
            if not q:
                break
            tried.append(q)
            hits = self.sx.search(q, k=6)
            self._last_raw = "\n".join(
                f"- {h['title']}：{h['content'][:300]}" for h in hits)
            self._last_urls = [h["url"] for h in hits]
            keep = [hits[i] for i in self._judge(topic, hits)]
            if keep:
                card = self._build_card(topic, keep)
                if card:
                    rec = {"topic": topic, "card": card,
                           "sources": [h["url"] for h in keep[:3]],
                           "tried": tried, "rounds": rnd + 1,
                           "built_at": time.strftime("%F %T")}
                    self.facts[topic] = rec
                    self._save()
                    # 入共享库，并记下**管用的那条检索式** —— 下次别的书查同类
                    # 主题时，把它当范例给规划模型看，比凭空想强
                    self.shared[sk] = dict(rec, era=self.era, good_query=q)
                    self._save_shared()
                    self.mem.add("fact", f"fact-{topic}", f"考据·{topic}", card)
                    return rec
            q = self._reformulate(topic, tried)

        # 全轮失败: 记下试过哪些式子, **不写死** —— 下次换了角度还能再试
        self.facts[topic] = {"topic": topic, "card": "", "sources": [],
                             "rejected": True, "tried": tried,
                             "built_at": time.strftime("%F %T")}
        self._save()
        return None

    def _build_card(self, topic: str, keep: List[Dict[str, Any]]) -> str:
        """把留下的结果压成事实卡 —— 只用检索源给的摘要, 不抓原文。

        抓正文看着能提质, 实际代价大于收益: 每条多一次 HTTP、超时与反爬各占一份,
        抓回来还得从整页导航广告里再剥一次正文。而按次计费的搜索源自带 summary,
        长度已经是 SearXNG 片段的一个数量级以上（600 字 vs 60-120 字），
        对「压成 5 条硬事实」这个用途足够了。
        真正提质的是**前面那道逐条过滤**, 不是往模型嘴里多塞几千字网页噪声。
        """
        parts = []
        for i, h in enumerate(keep[:5]):
            src = (h.get("content") or "").strip()
            if len(src) < 20:
                continue
            date = f"·{h['published']}" if h.get("published") else ""
            parts.append(f"【来源{i+1}｜{h.get('title','')[:60]}{date}】\n{src}")
        raw = "\n\n".join(parts) or self._last_raw
        if not self.summarize:
            return raw[:800]
        got = (self.summarize(
            f"下面是关于「{self.era} {topic}」的资料原文。\n"
            f"压成 5 条以内的**写作硬事实**，每条一句话，尽量带具体数字、"
            f"名称、年份。互相矛盾的标「存疑」；资料里没有的**不要补**。\n"
            f"只写与「{topic}」直接相关的，无关内容一律丢掉。\n"
            f"如果资料里确实没有能用的内容，只回复两个字：无\n"
            f"直接输出，无前言。\n\n{raw[:9000]}") or "").strip()
        if got in ("无", "", "None") or "无法生成" in got or "未包含" in got:
            return ""
        return got

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
        # 把**同时代查成过的检索式**当范例给它看。凭空想检索式的命中率不稳，
        # 而「这几条在同一个时代查出过东西」是现成的、便宜的经验。
        samples = [v.get("good_query") for k, v in list(self.shared.items())[-60:]
                   if v.get("good_query") and k.startswith(self._key(self.era) + "|")]
        ex_line = ("\n【同类题材查成过的检索式，照这个路数写】\n"
                   + "\n".join(f"- {q}" for q in samples[-6:])) if samples else ""
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
            f"{hint_line}{ex_line}\n\n"
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

        # 每个知识点要走「搜索 → 逐条判定 → 摘成卡片」三步，一步几秒，
        # 十几个主题串起来就是好几分钟 —— 而它们**彼此无关**，
        # 一个主题查什么不取决于另一个主题查到了什么。并发查。
        #
        # 已经有卡的直接取，不进线程池（那是纯读缓存）。
        todo = [nd for nd in needs if not (self.facts.get(nd["topic"]) or {}).get("card")]
        if todo:
            self._parallel_fetch(todo)

        blocks: List[str] = []
        for nd in needs:
            rec = self.facts.get(nd["topic"]) or {}
            card = rec.get("card")
            if card:
                blocks.append(f"【{nd['topic']}】{card.strip()}")
        return "\n\n".join(blocks)

    #: 并发查几个知识点。调高没用 —— 瓶颈在搜索源与判定模型的限流，
    #: 而且并发越高越容易触发对方的速率限制，反而更慢。
    FETCH_WORKERS = 4

    def _parallel_fetch(self, needs: List[Dict[str, str]]) -> None:
        """并发把这批知识点查回来。

        fact_for 会写 self.facts 并落盘，所以结果**必须回主线程合并** ——
        多个线程同时写同一个 dict 再各自 _save()，后写的会覆盖先写的，
        丢卡片还查不出原因。这里让工作线程只负责「查」，
        写入与落盘留在主线程一次做完。
        """
        from concurrent.futures import ThreadPoolExecutor

        def fetch(nd: Dict[str, str]):
            try:
                # 复制一份事实表给工作线程用，避免它顺手写主表
                saved, self_facts = self.facts, dict(self.facts)
                try:
                    self.facts = self_facts
                    rec = self.fact_for(nd)
                finally:
                    self.facts = saved
                return nd["topic"], (self_facts.get(nd["topic"]) or rec)
            except Exception as e:
                print(f"[retrieval] 「{nd.get('topic')}」查失败: {e}")
                return nd["topic"], None

        with ThreadPoolExecutor(max_workers=self.FETCH_WORKERS) as ex:
            got = list(ex.map(fetch, needs))
        n = 0
        for topic, rec in got:
            if rec and topic not in self.facts:
                self.facts[topic] = rec
                n += 1
                if rec.get("card"):
                    try:
                        self.mem.add("fact", f"fact-{topic}", f"考据·{topic}", rec["card"])
                    except Exception:
                        pass
        if n:
            self._save()

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

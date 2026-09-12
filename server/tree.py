"""树式分解：把一本书拆成带进出口合同的节点树。

—— 为什么要有这个文件 ——

原来的做法是「接着往下写」：模型写 1-20 章，把这 20 章念给它听，再写 21-40。
写到第 500 章，前面念不完，只能挑最近的念。于是它记不住第 30 章埋的东西，
也不知道第 800 章要发生什么，只能一路往前糊。实测后果：

  · 第 163 章的提示词 40125 字里，**73% 在复述过去**（最近 4 章正文 15072 +
    检索召回 7689 + 段落摘要 6618），真正说「这一章要写什么」的细纲只有 871 字、
    占 2%。台账其实是压过的（存量 42702 → 注入 3558），胖的不是台账，
    是**复述本身** —— 而复述量必然随书变长，这是线性写法的物理天花板。
  · 伏笔 159 条没收：排第 7 卷时第 8 卷的进口无处可查，伏笔没有可铺的方向
  · 章与章之间靠「最近几章正文原文」续接，隔一层就断

树的做法是工程分解：全书 1 个节点 → 阶段 → 卷 → 单元 → 章。拆任何一块时，
模型只需要看**它爹和它兄弟**：父节点的合同、左兄弟的出口、右兄弟的进口。
几千字，与全书 100 章还是 1 万章无关。而且它知道右兄弟要从哪开始，
等于知道未来，伏笔自然往那边铺。

—— 合同是表，不是散文 ——

原来的 stages.json 只有 exit 且是散文：
    exit: "肉身筑基大成（内壮五脏初显），手里有金钟罩正法传承，无人死亡…"
人能读，程序没法查。而且**只有出口没有进口**，所以「上一块出口 == 下一块进口」
这个等式根本没有左右两边 —— 我原来只能拿字面重合系数 ≥0.40 模糊告警。

这里改成结构化状态向量，逐字段比对，程序说了算，不用模型判断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from server.distill import soft

#: 账目值必须是**短的、离散的**记号(「3000人」「河北兵马元帅」), 不是描述。
#: 这是从原著反推出来的形态: 账本带计量单位, 数字比数字。
#: 一旦允许写成「狐寨石室中的鼎炉, 元阳已被吸过两次, 行尸预备」这种散文,
#: 精确匹配就必然失败 —— 实测让模型复述 28 个散文字段, 连着两轮 25 处违约。
ACCOUNT_MAX = 20


#: 模型给 due 写的「这条线已经收了」标记。它们不是节点 id。
#: 出口里带这种标记的线**根本不该还挂在 open_threads 上** —— open_threads 的
#: 意思就是「到这一刻还没收的线」。实测根出口三条线全带 due:"Closed",
#: 于是全树体检报「due 指向不存在的节点 C」, 改指向治不了根: 它们该被删掉。
#: 注意不含 "R" —— 根合同的线按提示词要求本来就写 due:"R"(全书之内收掉),
#: 那是正经节点 id, 误当收线标记会把根节点的线全删光。
_DUE_CLOSED = {"c", "closed", "close", "end", "done", "final", "-", "无",
               "已收", "已闭", "已结", "结束", "完结", "收掉"}


@dataclass
class Contract:
    """一个节点的进口或出口状态。

    分成**可数的**和**散文的**两堆, 只有可数的进不变式:

      accounts  可数的账(兵力/钱粮/名分/地盘/人手)。短记号, 精确匹配。
                原著里就是这个形态: 「9人→3000人」「40万贯→330万」
                「郓王→河北兵马元帅」。
      threads   有 id 的线。按 id 查生死, 不比文字。
      facts     已定死不许翻的事。只增不减。
      notes     主角在哪、谁是什么状态这类散文。**给模型看的上下文, 不做等值判断。**
                原来把它当不变式是根本性的错: 散文的任何改写都不相等。
    """

    accounts: Dict[str, str] = field(default_factory=dict)
    open_threads: List[Dict[str, Any]] = field(default_factory=list)
    #   每条 {"id": "t3", "what": "残碑上的武字疤是谁刻的", "due": "V7"}
    facts: List[str] = field(default_factory=list)
    notes: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def _as_map(v: Any) -> Dict[str, str]:
        """模型给的形状不保证是字典, 一律掰成字典。

        实测根合同那次 notes 返回的是**字符串列表**, dict() 直接
        ValueError 崩掉建树。解析层不兜住, 模型每换一种写法就崩一次。
        """
        if isinstance(v, dict):
            return {str(k): str(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            out = {}
            for i, x in enumerate(v):
                if isinstance(x, dict) and len(x) == 1:          # [{"甲":"乙"}]
                    k, val = next(iter(x.items()))
                    out[str(k)] = str(val)
                elif isinstance(x, dict):
                    for k, val in x.items():
                        out[str(k)] = str(val)
                else:                                            # ["一句话", ...]
                    s = str(x)
                    k, _, rest = s.partition("：")               # 「地点：迷离林」
                    out[k.strip() if rest else f"其{i+1}"] = (rest or s).strip()
            return out
        if isinstance(v, str) and v.strip():
            return {"说明": v.strip()}
        return {}

    @staticmethod
    def _as_list(v: Any) -> List[str]:
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v if str(x).strip()]
        if isinstance(v, dict):
            return [f"{k}：{x}" for k, x in v.items()]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "Contract":
        d = d or {}
        # 兼容早先的 hero/people/assets 三栏: 一律并进 notes(散文, 不比对),
        # 但 assets 里短到像账目的挪进 accounts。
        notes = Contract._as_map(d.get("notes"))
        acc = Contract._as_map(d.get("accounts"))
        for old_key in ("hero", "people", "assets"):
            for k, v in Contract._as_map(d.get(old_key)).items():
                if old_key == "assets" and len(str(v)) <= ACCOUNT_MAX:
                    acc.setdefault(k, str(v))
                else:
                    notes.setdefault(f"{k}", str(v))
        thr = []
        for x in (d.get("open_threads") or []):
            if isinstance(x, dict):
                y = dict(x)
                if str(y.get("due") or "").strip().lower() in _DUE_CLOSED:
                    continue            # 已经收了的线不算「没收的线」
                thr.append(y)
            elif str(x).strip():                    # 模型只给了一句话的线
                thr.append({"id": f"t{len(thr)+1}", "what": str(x).strip()})
        return Contract(
            accounts={k: str(v) for k, v in acc.items()},
            open_threads=thr,
            facts=Contract._as_list(d.get("facts")),
            notes=notes,
        )

    def brief(self, cap: int = 900) -> str:
        """给模型看的紧凑写法。**不带章号** —— 章号是 31 处正文自指的唯一来源。"""
        out = []
        if self.accounts:
            out.append("账本：" + "；".join(f"{k}={v}" for k, v in self.accounts.items()))
        if self.notes:
            out.append("；".join(f"{k}：{v}" for k, v in self.notes.items()))
        if self.open_threads:
            out.append("没收的线：" + "；".join(
                f"{t.get('what', '')}（须了结于 {t.get('due', '?')}）"
                for t in self.open_threads))
        if self.facts:
            out.append("已定死：" + "；".join(self.facts))
        s = "\n".join(out)
        return soft(s, cap, "合同摘要") + ("…" if len(s) > cap else "")


@dataclass
class Node:
    """树上的一个节点。全书是根，往下阶段/卷/单元/章。"""

    id: str                       # 根 "R"；子节点 "R.1"、"R.1.2"，层级一看便知
    level: str                    # book / stage / volume / unit / chapter
    title: str = ""
    line: str = ""                # 这一块的主线一句话：谁在解决什么
    start: int = 0                # 覆盖的章号区间（闭区间）
    end: int = 0
    entry: Contract = field(default_factory=Contract)
    exit: Contract = field(default_factory=Contract)
    children: List[str] = field(default_factory=list)
    # 但是链两字段 —— 从两本原著反推出的形态: 每节就这两句。
    # solves: 这一块把什么问题按下去了; exposes: **解法本身**生出了什么新问题。
    # 程序查: 下一块的 solves 必须**原样**是上一块的 exposes(照抄, 不许换说法)。
    solves: str = ""
    exposes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entry"] = self.entry.to_dict()
        d["exit"] = self.exit.to_dict()
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Node":
        return Node(
            id=d["id"], level=d.get("level", ""), title=d.get("title", ""),
            line=d.get("line", ""), start=int(d.get("start") or 0),
            end=int(d.get("end") or 0),
            entry=Contract.from_dict(d.get("entry")),
            exit=Contract.from_dict(d.get("exit")),
            children=list(d.get("children") or []),
            solves=str(d.get("solves") or ""),
            exposes=str(d.get("exposes") or ""),
        )


LEVELS = ["book", "stage", "volume", "unit", "chapter"]


def child_level(level: str) -> str:
    i = LEVELS.index(level) if level in LEVELS else 0
    return LEVELS[min(i + 1, len(LEVELS) - 1)]


# ─────────────────────────── 不变式 ───────────────────────────
# 这些是**程序**查的，不是模型判断的。模型总会换个说法，所以比对前先归一化，
# 但归一化只去标点空白，不做同义词匹配 —— 一旦允许模糊，等式就退回成「告警」，
# 而告警没人看就等于没有。

def _norm(v: Any) -> str:
    return re.sub(r"[\s，。、；：,.;:（）()【】\[\]「」『』\"'!！?？~—-]+", "", str(v or ""))


def _diff_map(a: Dict[str, str], b: Dict[str, str], label: str) -> List[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if _norm(a.get(k)) != _norm(b.get(k)):
            out.append(f"{label}「{k}」：出口={a.get(k, '—')} / 进口={b.get(k, '—')}")
    return out


def check_seam(prev: Node, nxt: Node) -> List[str]:
    """相邻兄弟：上一块的出口必须等于下一块的进口。返回违约清单（空=咬合）。"""
    a, b = prev.exit, nxt.entry
    errs: List[str] = []
    # 只比**可数的账**。notes 是散文, 散文的任何改写都不相等 —— 实测按散文
    # 做等值判断, 连着两轮 25 处违约且模型修不了。
    errs += _diff_map(a.accounts, b.accounts, "账目")
    ta = {str(t.get("id") or ""): t for t in a.open_threads}
    tb = {str(t.get("id") or ""): t for t in b.open_threads}
    for tid in sorted(set(ta) | set(tb)):
        if tid not in tb:
            errs.append(f"线「{ta[tid].get('what', tid)}」在出口还开着，进口却没了")
        elif tid not in ta:
            errs.append(f"线「{tb[tid].get('what', tid)}」进口凭空出现，出口没有")
    fa, fb = {_norm(x) for x in a.facts}, {_norm(x) for x in b.facts}
    for lost in fa - fb:
        errs.append(f"已定死的事实在进口丢了：{lost[:30]}")
    return errs


def check_parent(parent: Node, kids: List[Node]) -> List[str]:
    """父子：父的进口 == 长子进口；父的出口 == 幼子出口；区间要连续覆盖。"""
    errs: List[str] = []
    if not kids:
        return ["没有子节点"]
    head, tail = kids[0], kids[-1]
    errs += [f"父进口 vs 长子进口 — {e}" for e in
             check_seam(Node(id="", level="", exit=parent.entry), head)]
    # 幼子出口这一头**不能**照搬 check_seam。两条规矩本来就打架:
    #   · facts 只增不减 → 子块理所当然会添新事实
    #   · 父出口 == 幼子出口 → 父出口是在子块还不存在时写的, 不可能含有它们
    # 照搬的后果实测到了: 书层拆完报 20 处「已定死的事实在进口丢了」, 每一条
    # 都是子块新添的事实, **模型永远修不掉**, 白烧三轮重修 —— 跟当年让模型
    # 一字不差抄 28 条合同字段是同一个错。
    # 正确的方向是包含而不是相等: 父出口定死的事实, 幼子出口必须还留着;
    # 幼子多出来的, 由 settle_facts() 回灌给父出口。
    errs += [f"父出口 vs 幼子出口 — {e}" for e in
             check_seam(Node(id="", level="", exit=parent.exit),
                        Node(id="", level="", entry=tail.exit))]
    if head.start != parent.start or tail.end != parent.end:
        errs.append(f"区间没盖住父节点：父 {parent.start}-{parent.end}，"
                    f"子 {head.start}-{tail.end}")
    for i in range(len(kids) - 1):
        if kids[i].end + 1 != kids[i + 1].start:
            errs.append(f"{kids[i].id} 到 {kids[i+1].id} 章号断了："
                        f"{kids[i].end} → {kids[i+1].start}")
    return errs


def settle_facts(nodes: Dict[str, Node]) -> int:
    """把「事实只增不减」这条**由程序算死**，别拿它去问模型也别拿它报违约。

    事实沿章号单向累积, 这是纯粹的程序运算: 叶子按章序走一遍, 见一条记一条,
    每个叶子的进口 = 到它之前记下的全部, 出口 = 再加上它自己添的。内部节点的
    两头则取首末后代叶子 —— 因为父节点的两头**就是**首子的进口和幼子的出口。

    拆是自顶向下的, 子块添的事实回不到已经写好的父出口和右边兄弟的进口,
    于是每拆一层就多出一批谁也修不掉的违约。这个函数在每层拆完后跑一遍,
    把整棵树重新对齐。返回改动了几个节点。
    """
    root = nodes.get("R")
    leaves = sorted([n for n in nodes.values() if not n.children],
                    key=lambda x: (x.start, x.id))
    if not leaves:
        return 0
    run = list(root.entry.facts) if root else []
    seen = {_norm(x) for x in run}
    touched = 0
    for lf in leaves:
        if lf.entry.facts != run:
            lf.entry.facts, touched = list(run), touched + 1
        for f in lf.exit.facts:
            if _norm(f) not in seen:
                seen.add(_norm(f))
                run.append(f)
        lf.exit.facts = list(run)
    for nd in nodes.values():
        if not nd.children:
            continue
        desc = [l for l in leaves if l.start >= nd.start and l.end <= nd.end]
        if not desc:
            continue
        if nd.entry.facts != desc[0].entry.facts:
            nd.entry.facts, touched = list(desc[0].entry.facts), touched + 1
        if nd.exit.facts != desc[-1].exit.facts:
            nd.exit.facts, touched = list(desc[-1].exit.facts), touched + 1
    return touched


def check_threads(root: Node, nodes: Dict[str, Node]) -> List[str]:
    """线只能死在它该死的地方：due 指定的节点之内必须收掉，之后不许还开着。"""
    errs: List[str] = []
    for t in root.entry.open_threads + root.exit.open_threads:
        due = t.get("due")
        if due and due not in nodes:
            errs.append(f"线「{t.get('what', '')}」的 due={due} 指向不存在的节点")
    for nd in nodes.values():
        for t in nd.exit.open_threads:
            due = t.get("due")
            if not due or due not in nodes:
                continue
            d = nodes[due]
            # 这条线要在 due 节点内了结：那么 due 的出口里不该还有它
            if nd.end >= d.end and any(x.get("id") == t.get("id")
                                       for x in d.exit.open_threads):
                errs.append(f"线「{t.get('what', '')}」说好在 {due} 了结，"
                            f"但 {due} 的出口里它还开着")
    return errs


def check_progress(nd: Node) -> List[str]:
    """这一块必须真的把状态推动了 —— 出口不能和进口一模一样。

    「原地打转」不是文笔问题, 是**合同没变**: 读者读完一整卷, 主角还是那个身份、
    还在那个地方、手里还是那些东西。实测 163 章的书里战斗全走同一套流程,
    根子就在这里 —— 没有任何机制要求一块结束时世界得不一样。
    """
    a, b = nd.entry, nd.exit
    same_acc = all(_norm(a.accounts.get(k)) == _norm(b.accounts.get(k))
                   for k in set(a.accounts) | set(b.accounts))
    closed = {str(t.get("id")) for t in a.open_threads} -              {str(t.get("id")) for t in b.open_threads}
    if same_acc and not closed and len(b.facts) <= len(a.facts):
        return [f"「{nd.title or nd.id}」原地打转：账本一格没动、一条线没收、"
                f"没坐实任何新事实 —— 这一块读完，世界还是老样子"]
    return []


def changed_fields(nd: Node) -> frozenset:
    """这一块到底动了哪几格。用来判「是不是每块都在做同一件事」。"""
    out = set()
    for k in set(nd.entry.accounts) | set(nd.exit.accounts):
        if _norm(nd.entry.accounts.get(k)) != _norm(nd.exit.accounts.get(k)):
            out.add("账." + k)
    ea = {str(t.get("id")) for t in nd.entry.open_threads}
    xa = {str(t.get("id")) for t in nd.exit.open_threads}
    if ea - xa:
        out.add("收线")
    if len(nd.exit.facts) > len(nd.entry.facts):
        out.add("新定死的事实")
    return frozenset(out)


def check_variety(kids: List[Node], min_kinds: int = 2) -> List[str]:
    """兄弟之间不许每块都在做同一件事。

    「剧情太标」不是文笔问题, 是**每一块推动的是同一格**。实测那本 163 章的书,
    正面冲突全走一套流程(轻视→硬扛→打脸→交账), 读单章很爽, 连读就疲劳。
    根子是没有任何机制要求「这一块推动的东西和上一块不一样」。
    """
    if len(kids) < 3:
        return []
    sigs = [changed_fields(k) for k in kids]
    kinds = {s for s in sigs if s}
    if len(kinds) < min_kinds:
        only = "、".join(sorted(next(iter(kinds)))) if kinds else "什么都没动"
        return [f"{len(kids)} 块推动的是同一格（{only}）—— 连着读就是同一套流程"]
    # 连着三块签名完全一样也算
    for i in range(len(sigs) - 2):
        if sigs[i] and sigs[i] == sigs[i + 1] == sigs[i + 2]:
            return [f"{kids[i].id}~{kids[i+2].id} 连着三块动的是同一格"
                    f"（{'、'.join(sorted(sigs[i]))}）"]
    return []


#: 修真题材的通用词。它们在种子里到处都是, 拿来当「后文才出现的东西」会
#: 把根合同误判成违约。只挡**具体的人、地、物**。
_GENERIC = {"灵气", "内力", "修士", "门派", "肉身", "修行", "天下", "世道",
            "元气", "境界", "法术", "阵法", "符箓", "道门", "佛门", "旁门",
            "武修", "主角", "读者", "一个", "什么", "自己", "已经", "他的"}

#: 虚词。含虚词的短组合一律不是名物 —— 实测「是他」(出自「那是他的下场预告」,
#: 在种子里正好出现两次, 过了「出现两次」那道筛)被当成名物报了一次假违约,
#: 还把那一轮**最好的一版根合同**顶掉了, 换来更差的第三版。
_FUNC = set("的了是在和与也就都而但把被给从对向这那之其于及或并则已未不无"
            "有会能要很太再又还只我你他她它们个上下中人事时候样种做为以所"
            "因此如若使让第条来去出进过起点更非常")


def opening_scene(seed: str) -> Tuple[str, str]:
    """从种子里切出【开局落点】的**第一条**和其余几条。

    切不出来就返回两个空串 —— 种子没写这一栏是合法的, 不是错误。
    """
    m = re.search(r"【开局落点[^】]*】\s*\n?(.*?)(?=\n【|\Z)", seed or "", re.S)
    if not m:
        return "", ""
    body = m.group(1)
    items = re.split(r"(?m)^\s*(?=[一二三四五六七八九十]\s)", body)
    items = [x.strip() for x in items if x.strip()]
    if not items:
        return "", ""
    return items[0], "\n".join(items[1:])


def _entities(text: str, seed: str) -> set:
    """从一段话里抽出**具体的名物**: 2-4 字, 且在整颗种子里出现过至少两次。

    「出现两次」这一条是关键的降噪: 真正的人名地名物名(狐媚/虚弥戒/迷离林)
    在种子里必然反复出现, 而顺手一提的修饰词(光华/昏死)只出现一次。
    不靠分词, 因为分词器对自造名词一样切不准。
    """
    out = set()
    for run in re.findall(r"[一-鿿]{2,}", text or ""):
        for n in (4, 3, 2):
            for i in range(len(run) - n + 1):
                g = run[i:i + n]
                if g in _GENERIC:
                    continue
                # 二三字组带一个虚词就否掉; 四字组容一个(「罗刹海上」这类还能用)
                if sum(1 for ch in g if ch in _FUNC) > (1 if n >= 4 else 0):
                    continue
                if seed.count(g) >= 2:
                    out.add(g)
    # 「狐媚」和「狐媚献戒」都在时只留长的, 免得同一件事报两遍
    return {g for g in out if not any(g != h and g in h for h in out)}


def check_root_entry(root: "Node", seed: str) -> List[str]:
    """根合同的进口必须落在**第 1 章开场那一刻**。

    这条规矩在 p_root 里写了三遍(还附了实测案例), 模型照样把六章的开局
    落点整个压进 entry —— 实测这一版 entry 里躺着「狐媚已献虚弥戒」
    「石室行尸」「狐王已死」, 那是第六章末尾的世界, 不是第一章开场。
    后果不只是根节点难看: 幼子的进口由程序赋值 entry=prev_exit, 整条链的
    起点都跟着错, 开局那六章无处可写。

    凡是提示词里写了而程序不查的规矩, 早晚被违反 —— 这已经是第八次。
    种子里的【开局落点】是程序读得出来的, 那就由程序来查。
    """
    first, later = opening_scene(seed)
    if not first:
        return []
    # facts 是世界的规矩(「鼎炉被吸干必成行尸」), 开篇本就成立, 不参与判定。
    # 只查「主角此刻的账」和「此刻谁在哪」。
    now = " ".join(list(root.entry.accounts.values())
                   + list(root.entry.accounts.keys())
                   + list(root.entry.notes.values())
                   + list(root.entry.notes.keys()))
    errs = []
    e_first, e_later = _entities(first, seed), _entities(later, seed)
    早到 = sorted(g for g in (e_later - e_first) if g in now)
    # 一个词一条。挤成一条的话「一条里塞八个词」看起来比「两条」还轻,
    # 而三选一是按条数比的 —— 计数一失真, 选出来的就是错的那版。
    for g in 早到[:8]:
        errs.append(f"entry 里出现了「{g}」—— 那是开局落点后几条才有的东西，"
                    f"第 1 章开场时还不存在")
    if 早到:
        errs.append(f"entry 必须停在开局落点第一条那一刻：{first[:70]}")
    if e_first and not (e_first & set(_entities(now, seed))):
        errs.append(
            f"entry 里找不到开局落点第一条的任何东西"
            f"（该有：{'、'.join(sorted(e_first)[:6])}）")
    return errs


def audit_tree(nodes: Dict[str, Node]) -> List[str]:
    """全树体检。这是**唯一**判定树合不合法的地方，其余都不许自己判。"""
    errs: List[str] = []
    for nid, nd in sorted(nodes.items()):
        kids = [nodes[c] for c in nd.children if c in nodes]
        # 违约要能归到具体节点上, 否则前端的红点会打错地方 ——
        # audit 的输出既是给人看的, 也是前端定位用的, 格式必须统一成 [nid]。
        errs += [f"[{nid}] {e}" for e in (check_progress(nd) if nd.id != "R" else [])]
        if kids:
            errs += [f"[{nid}] {e}" for e in check_parent(nd, kids)]
            errs += [f"[{nid}] {e}" for e in check_variety(kids)]
            for i in range(len(kids) - 1):
                errs += [f"[{kids[i].id}→{kids[i+1].id}] {e}"
                         for e in check_seam(kids[i], kids[i + 1])]
    root = nodes.get("R")
    if root:
        errs += check_threads(root, nodes)
    return errs


# ─────────────────────── 拆一个节点要给模型看什么 ───────────────────────

def decomposition_context(node: Node, parent: Optional[Node],
                          left: Optional[Node], right: Optional[Node]) -> str:
    """拆这一块时的**全部**上下文：爹 + 左兄弟出口 + 右兄弟进口。

    不给全书前情，不给最近几章正文 —— 那正是「口述长篇」的做法。
    这里的字数与全书长度**无关**，永远是这几千字。
    """
    out = [f"【要拆的这一块】{node.title or node.id}（第 {node.start}-{node.end} 章）",
           f"　主线：{node.line}" if node.line else "",
           "\n【它进来时的状态】\n" + (node.entry.brief() or "（开篇，无前情）"),
           "\n【它出去时必须是这个状态】\n" + (node.exit.brief() or "（未定）")]
    if parent:
        out.append(f"\n【它属于】{parent.title or parent.id}：{parent.line}")
    if left:
        out.append("\n【上一块结束在哪】" + (left.line or "") + "\n"
                   + left.exit.brief(400))
    if right:
        out.append("\n【下一块要从哪开始】" + (right.line or "") + "\n"
                   + right.entry.brief(400)
                   + "\n　↑ 这一块的出口必须正好接上它。埋伏笔就往这个方向埋。")
    return "\n".join(x for x in out if x)


# ─────────────────────── 分解：模型提方案，程序判合法 ───────────────────────
# 模型不判断咬不咬合 —— 它只管提方案，audit_tree/check_* 判合法，
# 不合法就把**违约清单**打回去让它只修这几处。这是「程序能查，不用模型判断」
# 落地的样子：模型永远不会被问「你觉得这两块接得上吗」。

SCHEMA_HINT = """{"children":[{
  "title":"这一块叫什么（六到十四字，不许用书名）",
  "line":"这一块的主线一句话：谁在解决什么",
  "start":起始章号, "end":结束章号,
  "exit":{
    "accounts":{"金钟罩":"第二层","灵石":"300","名分":"苦役","人手":"3","左臂":"废"},
    "open_threads":[{"id":"t1","what":"还没揭开的那件事","due":"该在哪个节点内了结"}],
    "facts":["这一块坐实、以后不许翻的事"],
    "notes":{"处境":"他此刻在哪、和谁在一起、压着什么事（散文，随便写）"}}
}]}
账目值必须是**短记号**（20字以内，像记账），不许写成描述句。"""


def p_decompose(node: "Node", parent, left, right, k: int,
                extra: str = "") -> str:
    """拆一个节点。输入只有爹和左右兄弟，与全书长度无关。"""
    return f"""把下面这一块拆成 {k} 个连续的子块。

{decomposition_context(node, parent, left, right)}

规矩（这几条是程序会逐条查的，不是建议）：
1. 第一个子块的**进口**就是上面「它进来时的状态」，最后一个子块的**出口**
   必须**逐字段等于**上面「它出去时必须是这个状态」。
2. 每个子块只写 exit（进口由上一块的出口自动接上，你不用写）。
   相邻两块之间：前一块的 exit 就是后一块的进口，所以 exit 要写全，
   不许只写「变化的那部分」。
   **最后一块的 exit 不用你操心** —— 它必然等于上面「它出去时必须是这个状态」，
   程序会直接填。你只要保证倒数第二块的 exit 离那个状态**只差最后一步**。
3. 章号连续且不重叠，合起来正好盖满 {node.start}-{node.end}。
4. open_threads 里每条都要有 id 和 due。**一条线只能在 due 指定的那一块里了结**；
   在它之前的每一块 exit 里都要原样带着它，不许中途消失。
5. 每一块的 exit 要和它的进口**真的不一样** —— 至少主角的身份、位置、
   能力上限、伤里有一样变了，或者资源变了。原地打转的块不算数。
{extra}
只输出 JSON，不要代码围栏，不要解释：
{SCHEMA_HINT}"""


def _clip(t: str, n: int, what: str) -> str:
    """不硬切 —— 交给提炼层: 够 3:1 就真提炼, 不够就按句子边界收尾。

    原来是 t[:n], 半句话拦腰斩断, 而下游拿到的东西看起来是完整的。
    """
    from server.distill import distill
    return distill(t, n, what)


def p_repair(node: "Node", errs: List[str], last: str) -> str:
    return f"""你刚才给的拆法有 {len(errs)} 处违约。程序逐字段核对的结果：

{chr(10).join('· ' + e for e in errs[:20])}

只修这几处，别的不要动。仍然只输出 JSON，格式同前。

── 你上一版 ──
{_clip(last, 6000, "上一版拆法")}"""


def nd_id(node: "Node", i: int) -> str:
    return f"{node.id}.{i+1}"


def parse_children(raw: str, node: "Node") -> List["Node"]:
    """把模型给的 JSON 变成子节点，并把进口按「上一块出口」串好。"""
    import json as _j
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return []
    try:
        data = _j.loads(m.group(0))
    except Exception:
        return []
    lvl = child_level(node.level)
    out: List[Node] = []
    prev_exit = node.entry
    for i, c in enumerate(data.get("children") or []):
        if not isinstance(c, dict):
            continue
        ex = Contract.from_dict(c.get("exit"))
        # 「已定死的事实」只增不减, 由**程序**并进去, 不指望模型每次原样带着。
        # 实测老做法的后果: canon 300 条按章号排队, 只有最近 40 条(13%)进得了
        # 提示词, 第1-145章确立的 260 条模型完全看不到 —— 其中包括「戒空已死」
        # (师父)、「金钟罩第一层报废」(核心功法)、阿绣的 12 条状态。于是出现
        # 「金钟罩已坏却生效」「右臂突然恢复」这类硬伤: 不是模型忘了, 是没看见。
        # 永久事实和一次性事件混在一条时间队列里, 永久的必然被一次性的挤掉。
        _seen = {_norm(x) for x in prev_exit.facts}
        ex.facts = list(prev_exit.facts) + [f for f in ex.facts
                                            if _norm(f) not in _seen]
        # 没收的线同理: 只能在 due 指定的那一块里消失, 中途不许掉
        _open = {str(t.get("id") or ""): t for t in prev_exit.open_threads}
        _out = {str(t.get("id") or ""): t for t in ex.open_threads}
        for tid, t in _open.items():
            if tid not in _out and str(t.get("due") or "") != nd_id(node, i):
                ex.open_threads.append(t)
        nd = Node(
            id=f"{node.id}.{i+1}", level=lvl,
            title=str(c.get("title") or "")[:40],
            line=str(c.get("line") or "")[:120],
            start=int(c.get("start") or 0), end=int(c.get("end") or 0),
            entry=prev_exit,                      # ← 进口不许模型写，程序串
            exit=ex,
        )
        out.append(nd)
        prev_exit = nd.exit
    if out:
        # 幼子的出口**就是**父节点的出口 —— 这是定义, 不是要求。
        # 原来让模型自己复述一遍: 根节点 exit 有 28 个字段(hero4+people6+
        # assets7+线6+facts5), 等于要它一字不差抄 28 条字符串。实测连着两轮
        # 都是 25 处违约、数字一模一样 —— 它不是没修, 是这根本不是它能修的。
        # 和进口同一个道理: 能由程序定死的, 就不要问模型。
        last = out[-1]
        merged = Contract.from_dict(node.exit.to_dict())
        _seen = {_norm(x) for x in merged.facts}
        merged.facts += [f for f in last.exit.facts if _norm(f) not in _seen]
        last.exit = merged
    return out


def decompose(node: Node, parent, left, right, k: int, call,
              rounds: int = 3, log=None) -> Tuple[List[Node], List[str]]:
    """拆一块：模型提方案 → 程序验 → 把违约清单打回去让它只修这几处。

    模型永远不会被问「你觉得这两块接得上吗」—— 那是程序的活。
    返回 (子节点, 仍未解决的违约)。违约非空时由调用方决定是留下还是重来。
    """
    raw = call(p_decompose(node, parent, left, right, k))
    for i in range(rounds):
        kids = parse_children(raw, node)
        if not kids:
            errs = ["没解析出子节点"]
        else:
            errs = check_parent(node, kids) + check_variety(kids)
            for kid in kids:
                errs += check_progress(kid)
        if not errs:
            return kids, []
        if log:
            log(f"  拆 {node.id} 第 {i+1} 轮有 {len(errs)} 处违约，打回重修")
        if i == rounds - 1:
            return (kids if kids else []), errs
        raw = call(p_repair(node, errs, raw))
    return [], ["超出修复轮数"]


# ─────────────────────── 种子 → 根节点合同 ───────────────────────
# 「种子扩散」就是拆节点。但种子必须是**带进出口的节点**, 不能是几个关键词 ——
# 关键词不说明「某某那时候在不在」, 扩散出来的东西自然接不上。

def p_root(seed: str, chapters: int) -> str:
    return f"""下面是一本长篇小说的种子。把它变成**全书这一个节点**的合同。

全书共 {chapters} 章。

── 种子 ──
{seed}

合同要写两头：开篇时世界是什么样（entry），全书写完时世界是什么样（exit）。

⚠ **entry 就是第 1 章开场那一刻**，不是「主线真正开始」的那一刻。
种子里若有【开局落点】一栏，**必须以它第一条描述的场景为准**：那一条写的是
第 1 章，主角此刻在哪、是什么身份、手里有什么，就照抄进 entry。
实测踩过：种子第一条是「被围楼追杀、护着玉佩杀出重围」，模型自己跳到了
「已经穿越、已经被当成鼎炉」，于是开局那场戏整个没了，读者看着莫名其妙。
两头都写成**表**，不是描述。规矩：

· accounts：**可数的账**，像记账一样写短记号（每格 20 字以内）。
  必须包含：主角的功法层数、名分、伤（哪只手能用就写哪只）、钱、人手。
  这是全书要反复动的那几格 —— 原著的形态就是「9人→3000人」「郓王→兵马元帅」。
  开篇和结尾的数必须明显不同。
· open_threads：开篇就埋下、要在全书之内了结的大线。3-6 条，每条带 id 和 what。
  due 一律先写 "R"（拆到下面几层时再落到具体哪一块）。
· facts：开篇就已经坐实、全书不许翻的事（世界的规矩、主角的来历、
  已经发生过的不可逆的事）。
· notes：主角此刻的处境、关键人物各在哪（散文，随便写，最多 6 人）。

**不许出现现代专有名词**（地名、品牌、器物名）。主角的前世只能写成意象。
**不许出现章号**（「第N章」这种）。

只输出 JSON，不要代码围栏：
{{"title":"书名（八字以内）",
  "line":"全书一句话：谁用什么办法对付什么",
  "entry":{{"accounts":{{}},"open_threads":[],"facts":[],"notes":{{}}}},
  "exit":{{"accounts":{{}},"open_threads":[],"facts":[],"notes":{{}}}}}}"""


def parse_root(raw: str, chapters: int) -> Optional["Node"]:
    import json as _j
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = _j.loads(m.group(0))
    except Exception:
        return None
    entry = Contract.from_dict(d.get("entry"))
    exit_ = Contract.from_dict(d.get("exit"))
    # facts 只增不减对根自己也成立: 链会把 entry.facts 一路带到幼子出口,
    # 根出口若不含它们, 父子核对必然报「已定死的事实丢了」(实测 7 处全是这个)。
    seen = {_norm(x) for x in exit_.facts}
    exit_.facts = [f for f in entry.facts if _norm(f) not in seen] + exit_.facts
    return Node(id="R", level="book", title=str(d.get("title") or "")[:40],
                line=str(d.get("line") or "")[:160], start=1, end=chapters,
                entry=entry, exit=exit_)


# ─────────────────────── 里程碑链（方案乙的主体） ───────────────────────
# 不建四层树。全书 = 根合同 + 一条 30 节的里程碑链。
# 每节的形状**直接抄真书**: 从两本原著反推的分窗分析(L2_win)长这样 ——
# 段旗/入口/解法/反噬/账目/错算。反噬(exposes)必须是解法自身生出的新问题,
# 不是外部又来了敌人; 下一节的 solves 原样等于上一节的 exposes。

#: 真书里的一节, 当口味锚点用(大宋有种 L2 分窗实测产物, 非编造)。
MILESTONE_EXAMPLE = """\
解决：用信息差抢下「河北兵马元帅」名分, 拿钱买马带兵北上
但是：给胜捷军发双份钱粮的烧钱解法, 被点破家底撑不了一年
账目变动：名分 郓王→河北兵马元帅；兵力 9人→3000人；钱粮 40万贯→330万贯
错算：主角凭「红脸大胡子站在使者身边」把刘彦宗错当成郭药师射杀, 金军认定宋人设局, 彻底翻脸"""


def p_milestones(root: "Node", k: int, seed_chain: str = "") -> str:
    chain_seed = (("── 种子里的但是链（作者亲手写的主线骨架，必须逐条落地）──\n"
                   + seed_chain.strip()) if seed_chain.strip()
                  else "（种子里没给但是链，按下面的规矩自己拆）")
    # 种子写了但是链时, 第 i 节的「解决/但是」**就是种子第 i 条**。
    # 原来那条「第 i+1 节的 solves 原样照抄第 i 节的 exposes」跟作者种子的
    # 真实形状(八对「事件 → 后果」, 相邻两对之间本来就不是照抄关系)物理冲突:
    # 第 2 条的「解决：狐族被道门剿灭」并不等于第 1 条的「暴露：他能吸灵气
    # 被狐妖看见」。两条规矩不可能同时满足, 实测逼得三个候选全把配对错开
    # 一格 ——「解决：靠山把他当苦力」这种根本不是解决的句子就这么进来的。
    # 不是模型笨, 那是那条规矩逼出来的唯一走法。
    _rule2 = ("**第 i 节的 solves 和 exposes 就是种子【但是链】第 i 条的那两句**，"
              "原样照抄，一个字不许换，也不许挪到别的节去。"
              if parse_seed_chain(seed_chain) else
              "**第 i+1 节的 solves 必须原样照抄第 i 节的 exposes**，一个字都不许换。")
    return f"""全书要拆成 {k} 节里程碑。这是整本书的推进链, 不是目录。

【全书】《{root.title}》{root.line}
共 {root.end} 章。

【开局账本】
{root.entry.brief(700)}

【终局账本】
{root.exit.brief(700)}

**先读这条**：下面【种子里的但是链】是作者亲手写的主线骨架。
你的 {k} 节必须是**它的细分**，不是另起炉灶：每一条都要落到某一节上，
顺序不许调换，里面点名的人物和场所（谁收服他、他去哪做什么、谁把他丢给谁）
一个都不许丢。凭空造「斩追兵」「藏身形」这类通用填充节，等于没拆。

{chain_seed}

每一节写六样（口味参考, 这是从同类名作里实测反推的一节——
{MILESTONE_EXAMPLE}
）：

严格输出 JSON（不要围栏），格式：
{{"milestones":[{{
  "title":"这一节叫什么（6-14字, 不许用书名）",
  "solves":"这一节把什么问题按下去了（一句话）",
  "exposes":"**解法本身**生出了什么新问题（一句话。必须是解法的代价/副作用/它惊动了谁, 不许写「又来了个更强的敌人」）",
  "start":起始章, "end":结束章,
  "accounts":{{"要动的那几格账": "这一节结束时的新值(短记号,20字内)"}},
  "close":["这一节要收掉的线id"], "open":[{{"id":"新线id","what":"新埋的线"}}],
  "notes":{{"位置":"这一节结束时主角人在哪", "身边":"谁跟着他、谁在盯他",
           "处境":"此刻最要命的是什么"}},
  "miscalc":"这一节最重要的一次错算：谁把什么看成了什么, 因此做了什么"
}}]}}

硬规矩（程序逐条核对）：
1. 第 1 节的 solves 接开局危机；第 {k} 节结束时账本必须**逐格等于**终局账本。
2. {_rule2}
3. 章号连续盖满 1-{root.end}。
4. 每节至少动一格账（accounts 非空）, 且**不许连着三节都只动同一格**。
5. 开局账本里的线（{('、'.join(t.get('id','') for t in root.entry.open_threads)) or '无'}）
   每条都要被某一节 close 掉；close 与 open 的 id 不许凭空出现。"""


def parse_milestones(raw: str, root: "Node", chain: str = "") -> List["Node"]:
    import json as _j
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return []
    try:
        data = _j.loads(m.group(0))
    except Exception:
        return []
    out: List[Node] = []
    prev = root.entry
    open_pool = {str(t.get("id")): t for t in root.entry.open_threads}
    for i, c in enumerate(data.get("milestones") or []):
        if not isinstance(c, dict):
            continue
        ex = Contract(accounts=dict(prev.accounts), facts=list(prev.facts),
                      open_threads=[dict(t) for t in prev.open_threads],
                      notes=dict(prev.notes))
        for kk, vv in (c.get("accounts") or {}).items():
            ex.accounts[str(kk)] = str(vv)[:ACCOUNT_MAX + 10]
        # notes 也要读。原来这一栏**从头到尾没人读过**: 提示词没问, 解析不取,
        # 只有上面那句 notes=dict(prev.notes) 一路往下抄 —— 于是第 1 到第 7 卷
        # 的「此刻主角在哪」全是根节点那句「曼哈顿某废弃大楼顶层」。
        # 排纲拿它当「进这一卷时」的处境, 读到的是一句彻头彻尾的假话。
        # 程序替模型编它没说过的话, 比留空危险得多。
        #
        # **整栏替换, 不是逐键合并。** 合并版实测过一轮: 模型写的位置/身边
        # 覆盖掉了, 根节点其余的键(环境/状态/关键物/威胁/心态)却一路留着 ——
        # 第 7 卷位置已经是「罗刹海核心试炼场」, 环境还写着「暴雨夜, 枪火
        # 通明, 包围圈已收紧」。notes 是**某一刻的快照**, 快照不能打补丁:
        # 两个时刻的键拼在一起, 比其中任何一个单独拿出来都糟。
        _nt = Contract._as_map(c.get("notes"))
        if _nt:
            ex.notes = {str(k): str(v) for k, v in _nt.items()}
        closed = {str(x) for x in (c.get("close") or [])}
        ex.open_threads = [t for t in ex.open_threads
                           if str(t.get("id")) not in closed]
        for t in (c.get("open") or []):
            if isinstance(t, dict) and t.get("id"):
                t.setdefault("due", "R")
                ex.open_threads.append(dict(t))
                open_pool[str(t["id"])] = t
        nd = Node(id=f"R.{i+1}", level="volume",
                  title=str(c.get("title") or "")[:30],
                  line=str(c.get("miscalc") or "")[:140],
                  start=int(c.get("start") or 0), end=int(c.get("end") or 0),
                  entry=prev, exit=ex,
                  solves=str(c.get("solves") or "")[:120],
                  exposes=str(c.get("exposes") or "")[:120])
        out.append(nd)
        prev = ex
    # 种子写了但是链, 这两栏就**由程序照抄种子**, 不问模型。
    #
    # 为什么: 提示词原来要求「第 i+1 节的 solves 原样照抄第 i 节的 exposes」,
    # 而作者的种子根本不是那个形状 —— 它是八对(事件 → 后果), 第 2 条的
    # 「解决：狐族被道门剿灭」并不等于第 1 条的「暴露：他能吸灵气被狐妖看见」。
    # 两条规矩物理上不可能同时满足, 于是模型三个候选全把配对错开一格
    # (解决上一条的暴露 → 暴露下一条的解决), 「解决：靠山把他当苦力」这种
    # 根本不是解决的句子就这么进来了。不是模型笨, 那是那条规矩逼出来的唯一走法。
    #
    # 作者写了什么就是什么, 这是**程序抄得动**的事, 不该让模型转述。
    pairs = parse_seed_chain(chain)
    if pairs and len(out) == len(pairs):
        for nd, (sv, ex_) in zip(out, pairs):
            nd.solves, nd.exposes = sv[:120], ex_[:120]
    if out:
        # 幼子出口的账目/事实 = 根出口(程序定死)。但**线不覆盖** —— 线取链自己
        # 算出的结果: 若覆盖成根出口的线, 链上没人收的线会被一起洗掉,
        # 「开局埋的线没人收」就查不出来了(实测这个 bug 让该测试假绿转假红)。
        last = out[-1]
        merged = Contract.from_dict(root.exit.to_dict())
        seen = {_norm(x) for x in merged.facts}
        merged.facts += [f for f in last.exit.facts if _norm(f) not in seen]
        merged.open_threads = [dict(t) for t in last.exit.open_threads]
        last.exit = merged
    return out


def parse_seed_chain(chain: str) -> List[Tuple[str, str]]:
    """种子【但是链】→ [(解决, 暴露), ...]。作者亲手写的配对。

    每条形如「3 解决：X → 暴露：Y」(箭头也可能是 -> 或换行)。
    """
    out: List[Tuple[str, str]] = []
    for m in re.finditer(r"(?m)^\s*\d+\s*解决[：:]\s*(.+?)\s*(?:→|->|＝>)\s*"
                         r"暴露[：:]\s*(.+?)\s*$", chain or ""):
        out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def _same_gist(a: str, b: str, thresh: float = 0.5) -> bool:
    """两句话是不是同一句。模型会截断、会去掉标点, 所以按字重合算。"""
    sa, sb = set(_norm(a)), set(_norm(b))
    if not sa or not sb:
        return False
    return len(sa & sb) / min(len(sa), len(sb)) >= thresh


def check_seed_pairs(ms: List["Node"], pairs: List[Tuple[str, str]]) -> List[str]:
    """每一节的(解决, 暴露)必须是种子里**同一条**但是链, 不许错位。

    相邻咬合(下节 solves 照抄上节 exposes)是链**内部**的一致性, 它拦不住
    整体错位。实测抓到过: 模型把每节写成「解决第 i-1 条的暴露 → 暴露第 i 条
    的解决」, 相邻处处咬合、程序 0 违约, 可每节内部的因果全反了 ——
    「解决：靠山把他当苦力, 而且佛门有戒律」根本不是一个解决, 那是个麻烦。
    种子里那几对是作者亲手写的, 程序逐对核对就行, 没有理由不查。
    """
    errs: List[str] = []
    if not pairs or not ms:
        return errs
    for i, nd in enumerate(ms[:len(pairs)]):
        want_s, want_e = pairs[i]
        if not _same_gist(nd.solves, want_s):
            errs.append(f"{nd.id} 的「解决」不是种子第 {i+1} 条那个："
                        f"种子写的是「{want_s[:28]}」，这里写成了"
                        f"「{nd.solves[:28]}」")
        if not _same_gist(nd.exposes, want_e):
            errs.append(f"{nd.id} 的「但是」不是种子第 {i+1} 条那个："
                        f"种子写的是「{want_e[:28]}」，这里写成了"
                        f"「{nd.exposes[:28]}」")
    return errs


def check_milestones(root: "Node", ms: List["Node"],
                     chain: str = "") -> List[str]:
    """里程碑链体检: 但是链咬合(照抄判定) + 覆盖 + 变化多样性 + 线收干净。"""
    errs = check_parent(root, ms)
    pairs = parse_seed_chain(chain)
    errs += check_seed_pairs(ms, pairs)
    # 相邻逐字咬合这条只在**没有种子但是链**时才查。种子在的话它说了算:
    # 作者的链是八对(事件 → 后果), 相邻两对之间本来就不是照抄关系,
    # 拿照抄去要求它, 等于要求模型把作者写的东西改掉。
    if not pairs:
        for i in range(len(ms) - 1):
            if _norm(ms[i + 1].solves) != _norm(ms[i].exposes):
                errs.append(f"{ms[i].id}→{ms[i+1].id} 但是链断了："
                            f"上节暴露「{ms[i].exposes[:24]}」, "
                            f"下节解决的却是「{ms[i+1].solves[:24]}」(必须原样照抄)")
    errs += check_variety(ms)
    for nd in ms:
        errs += check_progress(nd)
        # 「此刻主角在哪」必须每节自己写。不写的话程序只能把上一节的原样抄
        # 下来, 于是第 1 到第 7 卷都写着开篇那句「曼哈顿某废弃大楼顶层」——
        # 排纲把它当「进这一卷时」的处境读, 读到的是假话。
        # 末节除外: 它的出口**就是**根出口, 由程序赋值, 不归模型写。
        if nd.exit.notes == nd.entry.notes and (not ms or nd.id != ms[-1].id):
            errs.append(f"{nd.id}「{nd.title[:10]}」没写 notes："
                        f"出这一节时主角在哪、身边有谁、最要命的是什么，"
                        f"跟进这一节时一字不差")
    # 开局的线必须都有归宿
    left = {str(t.get("id")) for t in (ms[-1].exit.open_threads if ms else [])}
    for t in root.entry.open_threads:
        if str(t.get("id")) in left:
            errs.append(f"开局埋的线「{t.get('what','')[:20]}」到终局还开着, 没人收")
    return errs


def score_milestones(root: "Node", ms: List["Node"], chain: str = "") -> float:
    """三候选选优的打分器。程序打分, 不问模型。

    合规是淘汰线不是加分项(违约越多分越低), 加分给**多样性**:
    动的账格种类越多越好 —— 温度拉满生三个候选, 谁不套路谁赢。
    """
    if not ms:
        return -1e9
    errs = check_milestones(root, ms, chain)
    kinds = {changed_fields(nd) for nd in ms if changed_fields(nd)}
    return -10.0 * len(errs) + 2.0 * len(kinds) + 0.5 * len(ms)

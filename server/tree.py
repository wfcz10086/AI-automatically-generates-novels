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

#: 合同的字段。改这张表要同时改 diff()/merge()，所以字段要少而稳。
#: 原则：只放**下一块必须知道**的东西。人物的性格、外貌、口癖不在这里
#: （那是人物卡，不随节点变），只有「他现在在哪、是什么身份」才在。
HERO_FIELDS = ("身份", "位置", "能力上限", "伤")


@dataclass
class Contract:
    """一个节点的进口或出口状态。固定大小，不随书变长。"""

    hero: Dict[str, str] = field(default_factory=dict)      # 主角：身份/位置/能力上限/伤
    people: Dict[str, str] = field(default_factory=dict)    # 关键人物 → 位置与状态（一句话）
    assets: Dict[str, str] = field(default_factory=dict)    # 资源：灵石/地盘/人手/凭证
    open_threads: List[Dict[str, Any]] = field(default_factory=list)
    #   每条 {"id": "t3", "what": "残碑上的武字疤是谁刻的", "due": "C2"}
    #   due = 该在哪个节点之内收掉。程序据此查「线有没有在它该死的地方死掉」。
    facts: List[str] = field(default_factory=list)          # 已定死、不可翻的事

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "Contract":
        d = d or {}
        return Contract(
            hero=dict(d.get("hero") or {}),
            people=dict(d.get("people") or {}),
            assets=dict(d.get("assets") or {}),
            open_threads=[dict(x) for x in (d.get("open_threads") or [])
                          if isinstance(x, dict)],
            facts=[str(x) for x in (d.get("facts") or [])],
        )

    def brief(self, cap: int = 900) -> str:
        """给模型看的紧凑写法。**不带章号** —— 章号是 31 处正文自指的唯一来源。"""
        out = []
        if self.hero:
            out.append("主角：" + "｜".join(f"{k}={v}" for k, v in self.hero.items() if v))
        if self.people:
            out.append("人在哪：" + "；".join(f"{k}={v}" for k, v in self.people.items()))
        if self.assets:
            out.append("手里有：" + "；".join(f"{k}={v}" for k, v in self.assets.items()))
        if self.open_threads:
            out.append("没收的线：" + "；".join(
                f"{t.get('what', '')}（须了结于 {t.get('due', '?')}）"
                for t in self.open_threads))
        if self.facts:
            out.append("已定死：" + "；".join(self.facts))
        s = "\n".join(out)
        return s if len(s) <= cap else s[:cap] + "…"


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
    errs += _diff_map(a.hero, b.hero, "主角")
    errs += _diff_map(a.people, b.people, "人物")
    errs += _diff_map(a.assets, b.assets, "资源")
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
    errs += [f"父出口 vs 幼子出口 — {e}" for e in
             check_seam(tail, Node(id="", level="", entry=parent.exit))]
    if head.start != parent.start or tail.end != parent.end:
        errs.append(f"区间没盖住父节点：父 {parent.start}-{parent.end}，"
                    f"子 {head.start}-{tail.end}")
    for i in range(len(kids) - 1):
        if kids[i].end + 1 != kids[i + 1].start:
            errs.append(f"{kids[i].id} 到 {kids[i+1].id} 章号断了："
                        f"{kids[i].end} → {kids[i+1].start}")
    return errs


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
    same_hero = all(_norm(a.hero.get(k)) == _norm(b.hero.get(k))
                    for k in set(a.hero) | set(b.hero))
    same_asset = all(_norm(a.assets.get(k)) == _norm(b.assets.get(k))
                     for k in set(a.assets) | set(b.assets))
    if same_hero and same_asset:
        return [f"「{nd.title or nd.id}」原地打转：主角的身份/位置/能力/伤没变，"
                f"资源也没变 —— 这一块读完，世界还是老样子"]
    return []


def changed_fields(nd: Node) -> frozenset:
    """这一块到底动了哪几格。用来判「是不是每块都在做同一件事」。"""
    out = set()
    for k in set(nd.entry.hero) | set(nd.exit.hero):
        if _norm(nd.entry.hero.get(k)) != _norm(nd.exit.hero.get(k)):
            out.add("主角." + k)
    for k in set(nd.entry.assets) | set(nd.exit.assets):
        if _norm(nd.entry.assets.get(k)) != _norm(nd.exit.assets.get(k)):
            out.add("资源." + k)
    for k in set(nd.entry.people) | set(nd.exit.people):
        if _norm(nd.entry.people.get(k)) != _norm(nd.exit.people.get(k)):
            out.add("人物." + k)
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
    "hero":{"身份":"…","位置":"…","能力上限":"…","伤":"…"},
    "people":{"某人":"他此刻在哪、是什么状态"},
    "assets":{"灵石":"…","地盘":"…"},
    "open_threads":[{"id":"t1","what":"还没揭开的那件事","due":"该在哪个节点内了结"}],
    "facts":["这一块坐实、以后不许翻的事"]}
}]}"""


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
3. 章号连续且不重叠，合起来正好盖满 {node.start}-{node.end}。
4. open_threads 里每条都要有 id 和 due。**一条线只能在 due 指定的那一块里了结**；
   在它之前的每一块 exit 里都要原样带着它，不许中途消失。
5. 每一块的 exit 要和它的进口**真的不一样** —— 至少主角的身份、位置、
   能力上限、伤里有一样变了，或者资源变了。原地打转的块不算数。
{extra}
只输出 JSON，不要代码围栏，不要解释：
{SCHEMA_HINT}"""


def p_repair(node: "Node", errs: List[str], last: str) -> str:
    return f"""你刚才给的拆法有 {len(errs)} 处违约。程序逐字段核对的结果：

{chr(10).join('· ' + e for e in errs[:20])}

只修这几处，别的不要动。仍然只输出 JSON，格式同前。

── 你上一版 ──
{last[:6000]}"""


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

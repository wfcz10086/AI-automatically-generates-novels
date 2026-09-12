"""阶段骨架 · 功能位 · 关系张力 —— 让人物围绕目标组织，而不是靠一张静态花名册。

## 为什么需要这一层

原来的花名册是**开书时一次性生成的静态名单**，排纲提示词里写死
「只能从中挑，不得凭空造人」。于是实测出现三类结构性塌陷：

  · 14 个人撑 328 章。总纲点名的角色没进花名册就等于不存在 ——
    某书总纲明写「林冲欠下旧案人情，留到第四幕引爆」，林冲不在册，
    全书出现 2 次，第四幕零出现，那个引爆点永远不会响。
  · 主角最大的情感债（潘金莲）出场 2 章后消失。不是模型健忘，
    是阶段目标推进到第二级之后，**结构里没有给她任何位置**。
  · 最强战力（武松）出场 238 章，功能位从头到尾是「执行者」，
    从不为难 —— 因为没有任何东西记着「他和主角之间压着一条人命」。

这三件事是同一个病：**角色不该是名单，该是阶段目标的派生物。**

## 模型

    阶段目标  →  必要步骤  →  功能位  →  人

「夺不死木」和「当皇帝」是同一个结构：目标拆成步骤，每个步骤要有人挡、
有人给、有人抢、有人背叛、有人付代价、有人看着。六个功能位对所有题材通用，
只有叫法随题材变（修仙叫护法/道敌，军事叫上峰/敌将，实质是同六位）。

人数因此是**算出来的**，不是配额拍出来的：阶段数 × 功能位 − 跨阶段复用。
而复用比新增更有力 —— 同一个人在不同阶段占不同功能位，就是人物弧光的
机械定义。

## 张力账（第 10 本台账）

现有 9 本台账记的都是**状态**（谁有多少钱、谁在什么位置、谁死了），
缺的是记**关系里压着什么**。张力的铁律只有一条：

    张力只能通过明写的事件转化，不能靠一方消失来消解。

这条一立，两个角色就互相拴住：只要金莲还在，武松就得定期出现内心账；
武松每出现一次，金莲就不能被忘掉。断线问题自己就消失了。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 功能位

#: 六个通用功能位。key 是机器用的槽名，label 是默认叫法，hint 说明这一位
#: 在故事里干什么。题材包可以只改 label（修仙的「阻挡者」叫「护山长老」），
#: 但槽本身不增不减 —— 增槽等于把题材知识写进引擎。
ROLE_SLOTS: List[Dict[str, str]] = [
    {"key": "giver", "label": "给予者",
     "hint": "手里有主角这一步必需的东西（钱/权/情报/门路/传承），肯给或能被拿到"},
    {"key": "blocker", "label": "阻挡者",
     "hint": "站在这一步正前方，不打倒或不绕过就过不去"},
    {"key": "rival", "label": "同争者",
     "hint": "想要同一样东西的人。与阻挡者不同：他不是挡路，他是抢"},
    {"key": "traitor", "label": "内应",
     "hint": "主角自己人里通着对面的那个，或对面阵营里能被主角策反的那个"},
    {"key": "cost", "label": "代价承受者",
     "hint": "这一步的账单落在谁身上。必须是主角在乎的人，否则代价不成立"},
    {"key": "witness", "label": "见证者",
     "hint": "看着这一切发生并记着的人，读者的眼睛与全书的账本"},
]

SLOT_KEYS = [s["key"] for s in ROLE_SLOTS]
_SLOT_LABEL = {s["key"]: s["label"] for s in ROLE_SLOTS}

#: 功能位里允许留空的两个。给予者/阻挡者/代价承受者缺位，这一阶段就没有戏；
#: 同争者与内应不是每个阶段都该有，硬凑反而假。
REQUIRED_SLOTS = ("giver", "blocker", "cost", "witness")


def slot_labels(genre: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """本书六个功能位的叫法。题材包 role_slots 只能改 label，不能增删槽。"""
    out = dict(_SLOT_LABEL)
    for k, v in ((genre or {}).get("role_slots") or {}).items():
        if k in out and isinstance(v, str) and v.strip():
            out[k] = v.strip()[:12]
    return out


def slot_menu(genre: Optional[Dict[str, Any]] = None) -> str:
    """给模型看的功能位说明块。"""
    lab = slot_labels(genre)
    return "\n".join(f"- {lab[s['key']]}（{s['key']}）：{s['hint']}"
                     for s in ROLE_SLOTS)


# ---------------------------------------------------------------- JSON 兜底

def parse_json(text: str, want: Any = "") -> Dict[str, Any]:
    """从模型输出里抠出目标 JSON 对象。

    括号配平逐个试解析 —— 模型爱在 JSON 前后加解释、加代码围栏、
    甚至在 JSON 之后再补一段说明，直接 json.loads 十次有三次挂。

    `want` 可以是一个键名或一组键名；**不传 want 是个坑**：返回的是第一个
    配平的对象，而外层对象里但凡嵌了子对象（"plant":[{"ch":88,...}]），
    第一个配平的往往是那个**子对象** —— 于是调用方 .get("plant") 拿到 None，
    整批状态静默记成零。实测排纲巡检有一半批次因此白跑。
    所以：嵌套结构一律把外层的键传进来。
    """
    keys = [want] if isinstance(want, str) else list(want or [])
    keys = [k for k in keys if k]
    raw = re.sub(r"^```[a-z]*\s*|\s*```$", "", (text or "").strip(), flags=re.M)
    best: Dict[str, Any] = {}
    for m in re.finditer(r"\{", raw):
        depth, end = 0, None
        for i in range(m.start(), len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if not end:
            continue
        try:
            cand = json.loads(raw[m.start():end])
        except Exception:
            continue
        if not isinstance(cand, dict):
            continue
        if not keys:
            return cand
        if any(k in cand for k in keys):
            # 命中的里面取最外层(最长的那个), 防止外层还没试到就被子对象截胡
            if len(raw[m.start():end]) > len(json.dumps(best, ensure_ascii=False)) or not best:
                best = cand
    return best


# ---------------------------------------------------------------- 名字归一

#: 群体前缀。功能位允许由一个群体承担（代价常常落在「阵亡的弟兄」身上），
#: 但群体不是角色，不该被当成「未登记角色」去催注册。
GROUP_PREFIX = "群体·"

_PAREN = re.compile(r"[（(【\[].*?[）)】\]]")


def canon_name(name: str, aliases: Optional[Dict[str, str]] = None) -> str:
    """角色名归一 —— 括注剥掉，别名并到本名。

    不做这一步会出两类误报，实测都撞上了：
      · 骨架里写「何九叔（刑名内线）」，花名册里是「何九叔」→ 报成未登记角色
      · 主角在档案里叫「西门庆（林远）」，出场角色栏里写「西门庆」
        → 判定主角 346 章没出过场，张力全部报「静默消解」
    """
    s = _PAREN.sub("", str(name or "")).strip().strip("·、,，/")
    if s.startswith(GROUP_PREFIX):
        s = s[len(GROUP_PREFIX):].strip()
    return (aliases or {}).get(s, s)


def is_group(name: str) -> bool:
    """这一位是群体而不是具名人物。

    判断交给模型（排纲时按格式契约标注 群体·），这里只认标记，
    不做「名字里有没有『们』字」这种规则猜测 —— 猜不准，也猜不完。
    """
    return str(name or "").strip().startswith(GROUP_PREFIX)


# ---------------------------------------------------------------- 出场统计

_CAST_LINE = re.compile(r"^出场角色[：:]\s*(.+)$", re.M)


def cast_appearances(outlines: Dict[str, str],
                     before: Optional[int] = None,
                     aliases: Optional[Dict[str, str]] = None) -> Dict[str, List[int]]:
    """从已排细纲里统计每个角色出现在哪些章。

    章纲里「出场角色：」是四种内容类型共用的字段（prompt_compiler 统一要求），
    所以这个统计对小说/剧本/短剧/动漫都成立 —— 排纲阶段唯一拿得到的连续性数据。
    """
    app: Dict[str, List[int]] = {}
    for k, v in (outlines or {}).items():
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        if before is not None and n >= before:
            continue
        m = _CAST_LINE.search(str(v))
        if not m:
            continue
        # 先剥括注再切分隔符 —— 括注里常有逗号（「蒋门神（未露面，遣军汉出面）」），
        # 直接切会切出「蒋门神（未露面」和「遣军汉出面）」两个假角色，
        # 前端人物统计里就会冒出这种从不存在的人。
        line = _PAREN.sub("", m.group(1))
        for nm in re.split(r"[、,，/｜|]", line):
            nm = canon_name(nm, aliases)
            # 名字长度设上限是为了滤掉「一群围观的百姓」这类描述性词组
            if nm and 1 < len(nm) <= 8:
                app.setdefault(nm, []).append(n)
    for v in app.values():
        v.sort()
    return app


def mention_counts(outlines: Dict[str, str], names: Sequence[str],
                   before: Optional[int] = None) -> Dict[str, List[int]]:
    """某些名字在细纲正文里被提到的章号（不限于出场角色栏）。

    与 cast_appearances 的区别：一个人可以被反复提及却从不出场 ——
    那正是「总纲承诺了他、白名单挡住他」的典型形状。
    """
    out: Dict[str, List[int]] = {n: [] for n in names}
    for k, v in (outlines or {}).items():
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        if before is not None and n >= before:
            continue
        t = str(v)
        for nm in names:
            if nm and nm in t:
                out[nm].append(n)
    for v in out.values():
        v.sort()
    return out


# ---------------------------------------------------------------- 阶段骨架

def build_stages(*, outline: str, total_chapters: int, title: str = "",
                 genre: Optional[Dict[str, Any]] = None,
                 roster: Optional[Sequence[str]] = None,
                 ask: Callable[[str], str]) -> List[Dict[str, Any]]:
    """把总纲读成结构化的阶段骨架。

    总纲里的台阶写得再好，只要它是散文，机器就读不到 —— 实测某书总纲
    A 卷写着「每一级台阶付真代价：码头买扑的银子是替武松顶军法换来的赦」，
    这句话从没影响过任何一章的排纲，因为没有任何代码读过它。
    """
    if not (outline or "").strip():
        return []
    lab = slot_labels(genre)
    prompt = (
        f"你在给长篇作品《{title}》做**阶段骨架**。下面是总纲。\n\n"
        f"把全书拆成 4-9 个阶段（按已有的卷/幕/台阶划分，别另起炉灶）。"
        f"每个阶段回答四件事：\n"
        f"① 这一阶段主角要达成的**一个**核心目标（一句话，要能判断达没达成）\n"
        f"② 达成它必须走的 3-6 个**步骤**（每步是一件具体的事，不是形容）\n"
        f"③ 这一阶段的**功能位**分别由谁来占：\n{slot_menu(genre)}\n"
        f"④ 离开这一阶段时的**状态线**（实力到哪、手里有什么、谁死了）\n\n"
        f"功能位填人名。**优先复用已有角色**——同一个人在不同阶段占不同功能位，"
        f"那是人物成长，比新造一个人有力得多。已有角色："
        f"{'、'.join(roster or []) or '（尚无）'}\n"
        f"总纲里点了名但不在上面这份名单里的人，照样填 —— 他们需要被登记。\n"
        f"确实找不到人的位置留空数组，不要硬凑。\n"
        f"**只填名字本身，不要加括注说明**（写「何九叔」，不写「何九叔（刑名内线）」）。\n"
        f"这一位若由一个群体承担（阵亡的弟兄、流民、朝中掣肘者），"
        f"写成「{GROUP_PREFIX}阵亡的护商队弟兄」——群体不是角色，不需要登记。\n\n"
        f"全书共 {total_chapters} 章。只输出 JSON，不要代码围栏，不要附加说明：\n"
        '{"stages":[{"name":"A·阶段名","start":1,"end":50,'
        '"goal":"一句话目标","steps":["步骤1","步骤2"],'
        '"roles":{"giver":["甲"],"blocker":["乙"],"rival":[],'
        '"traitor":[],"cost":["丙"],"witness":["丁"]},'
        '"exit":"离开本阶段时的状态线"}]}\n\n'
        f"#总纲\n{clip(outline, 14000, '总纲(阶段骨架)')}")
    data = parse_json(ask(prompt), "stages")
    out: List[Dict[str, Any]] = []
    for s in (data.get("stages") or [])[:12]:
        if not isinstance(s, dict):
            continue
        try:
            start, end = int(s.get("start") or 0), int(s.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if not (1 <= start <= end <= max(total_chapters, end)):
            continue
        roles = {k: [str(x).strip()[:12] for x in (s.get("roles") or {}).get(k, [])
                     if str(x).strip()][:4] for k in SLOT_KEYS}
        out.append({
            "name": str(s.get("name") or f"第{start}-{end}章")[:40],
            "start": start, "end": end,
            "goal": str(s.get("goal") or "")[:120],
            "steps": [str(x)[:60] for x in (s.get("steps") or [])][:8],
            "roles": roles,
            "exit": str(s.get("exit") or "")[:200],
            "labels": lab,
        })
    out.sort(key=lambda x: x["start"])
    return out


def stage_of(stages: Sequence[Dict[str, Any]], n: int) -> Optional[Dict[str, Any]]:
    for s in stages or []:
        if s["start"] <= n <= s["end"]:
            return s
    return None


def stage_brief(stage: Optional[Dict[str, Any]], n: int = 0) -> str:
    """注入排纲的阶段约束块。"""
    if not stage:
        return ""
    lab = stage.get("labels") or _SLOT_LABEL
    lines = [f"【本阶段：{stage['name']}（第{stage['start']}-{stage['end']}章）】",
             f"阶段目标：{stage['goal']}"]
    if stage.get("steps"):
        lines.append("必经步骤：" + " → ".join(stage["steps"]))
    who = [f"{lab.get(k, k)}={'、'.join(v)}"
           for k, v in (stage.get("roles") or {}).items() if v]
    if who:
        lines.append("本阶段功能位：" + "；".join(who))
    if stage.get("exit"):
        lines.append(f"离开本阶段时必须达到：{stage['exit']}")
    if n:
        span = max(1, stage["end"] - stage["start"] + 1)
        pct = int((n - stage["start"]) * 100 / span)
        lines.append(f"当前进度：本阶段第 {n - stage['start'] + 1} 章 / 共 {span} 章（{pct}%）")
    return "\n".join(lines)


def vacancies(stage: Optional[Dict[str, Any]]) -> List[str]:
    """本阶段空着的必填功能位 —— 空一个就少一条戏。"""
    if not stage:
        return []
    lab = stage.get("labels") or _SLOT_LABEL
    roles = stage.get("roles") or {}
    return [lab.get(k, k) for k in REQUIRED_SLOTS if not roles.get(k)]


def unregistered(stages: Sequence[Dict[str, Any]], roster: Sequence[str],
                 aliases: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]:
    """被阶段骨架点了名、却不在花名册里的人。

    这些正是「总纲承诺了、白名单挡住了」的那批 —— 必须补登记，
    否则排纲时模型没有权限写他们。
    """
    known = {canon_name(x, aliases) for x in roster if str(x).strip()}
    seen: Dict[str, str] = {}
    for s in stages or []:
        for k, names in (s.get("roles") or {}).items():
            for raw in names:
                if is_group(raw):          # 群体承担的功能位不需要注册
                    continue
                nm = canon_name(raw, aliases)
                if nm and nm not in known and nm not in seen:
                    seen[nm] = f"{s['name']}·{(s.get('labels') or _SLOT_LABEL).get(k, k)}"
    return sorted(seen.items(), key=lambda kv: kv[0])


def arc_frozen(stages: Sequence[Dict[str, Any]], min_span: int = 3,
               aliases: Optional[Dict[str, str]] = None) -> List[str]:
    """连续 min_span 个阶段功能位没变过的角色 —— 人物没有弧光。

    实测某书最强战力出场 238 章，从头到尾是执行者，读起来像个工具。
    """
    hist: Dict[str, List[str]] = {}
    for s in stages or []:
        for k, names in (s.get("roles") or {}).items():
            for raw in names:
                if is_group(raw):
                    continue
                hist.setdefault(canon_name(raw, aliases), []).append(k)
    out = []
    for nm, ks in hist.items():
        if len(ks) >= min_span and len(set(ks)) == 1:
            lab = (stages[0].get("labels") or _SLOT_LABEL).get(ks[0], ks[0])
            out.append(f"{nm}（连续 {len(ks)} 个阶段都是「{lab}」，没有变化）")
    return out


# ---------------------------------------------------------------- 张力账

def build_tensions(*, outline: str, characters: str = "", title: str = "",
                   ask: Callable[[str], str]) -> List[Dict[str, Any]]:
    """从总纲与人物档案里读出不可调和的关系张力。

    只记**真正互斥**的：主角同时想要的两件事彼此冲突，或两个人之间压着
    一笔算不清的账。「性格不合」「暂时误会」不算 —— 那些自己会解决。
    """
    src = (outline or "") + "\n\n" + (characters or "")
    if not src.strip():
        return []
    prompt = (
        f"下面是长篇作品《{title}》的总纲与人物档案。\n\n"
        f"找出其中**不可调和的关系张力**：两个人之间压着一笔算不清的账，"
        f"或主角同时想要的两件事彼此互斥。\n"
        f"判据是「这件事不可能让双方都满意」。性格不合、暂时误会、"
        f"立场分歧**都不算** —— 那些自己会解决。\n"
        f"最多 6 条，按分量排序。\n\n"
        f"每条要说清：因什么而起、为什么无解、现在压着还是已经爆了、"
        f"压着的话谁在付代价。\n\n"
        f"只输出 JSON，不要代码围栏：\n"
        '{"tensions":[{"between":["甲","乙"],"about":"因某人某事",'
        '"why_unsolvable":"为什么不可能两全","state":"压着",'
        '"cost":"压着期间谁在付什么代价"}]}\n'
        f"state 只能取：压着 / 已爆发 / 已了结。没有就输出 {{\"tensions\":[]}}。\n\n"
        f"{clip(src, 12000, '总纲(张力账)')}")
    data = parse_json(ask(prompt), "tensions")
    out = []
    for t in (data.get("tensions") or [])[:8]:
        if not isinstance(t, dict):
            continue
        who = [str(x).strip()[:12] for x in (t.get("between") or []) if str(x).strip()]
        if len(who) < 2:
            continue
        st = str(t.get("state") or "压着").strip()
        out.append({
            "between": who[:3],
            "about": str(t.get("about") or "")[:100],
            "why_unsolvable": str(t.get("why_unsolvable") or "")[:160],
            "state": st if st in ("压着", "已爆发", "已了结") else "压着",
            "cost": str(t.get("cost") or "")[:120],
            "last_touched": 0,
        })
    return out


def tension_brief(tensions: Sequence[Dict[str, Any]], n: int = 0) -> str:
    """注入排纲/写作的张力约束块。"""
    live = [t for t in (tensions or []) if t.get("state") != "已了结"]
    if not live:
        return ""
    lines = ["【关系张力（压着的账，不许假装不存在）】"]
    for t in live[:6]:
        gap = (f"，已 {n - t['last_touched']} 章没碰"
               if n and t.get("last_touched") else "")
        lines.append(f"- {' ↔ '.join(t['between'])}：{t['about']}"
                     f"（{t['state']}{gap}）")
        if t.get("cost"):
            lines.append(f"  压着的代价：{t['cost']}")
    lines.append("铁律：张力只能被**明写的事件**推动（爆发／以代价换暂压／"
                 "转移到第三方／了结），**不许靠让某一方消失来消解**。")
    return "\n".join(lines)


def silent_resolution(tensions: Sequence[Dict[str, Any]],
                      appearances: Dict[str, List[int]],
                      upto: int, gap: int = 25,
                      aliases: Optional[Dict[str, str]] = None) -> List[str]:
    """张力被静默消解 —— 一方悄悄消失，账就这么没了。

    这是长篇最隐蔽的塌陷：没有任何一章写错，但主角最大的情感债
    在第 10 章之后再没出现过，而读者一直等着它被还。
    """
    out = []
    for t in tensions or []:
        if t.get("state") == "已了结":
            continue
        who = [canon_name(x, aliases) for x in (t.get("between") or [])]
        # 内在张力（主角与自己的矛盾）只有一个真实当事人 —— 拿「某某——内在
        # 已 346 章没出场」去报静默消解是纯误报：那本来就不是一个会出场的人。
        if len(set(w.split("——")[0] for w in who if w)) < 2:
            continue
        missing = []
        for raw in t.get("between") or []:
            nm = canon_name(raw, aliases)
            if nm not in appearances and "——" in nm:
                continue
            cs = appearances.get(nm) or []
            last = cs[-1] if cs else 0
            # **从未登场的人不算「消失」**。「静默消解」的语义是「本来在场,
            # 然后悄悄没了」; 一个还没出场的角色(总纲安排他第 221 章才来)
            # 被报成「已断 160 章」是纯误报, 而且它排在前面, 会把真账
            # (潘金莲末次出场第 103 章、已断 57 章)挤出纠偏清单。
            # 没登场是另一回事 —— 那属于「该来的还没来」, 不归这个检测器管。
            if not cs:
                continue
            if upto - last >= gap:
                missing.append(f"{nm}（末次出场第 {last or 0} 章，已断 {upto - last} 章）")
        if missing:
            out.append(f"「{' ↔ '.join(t['between'])}：{t['about']}」仍未了结，"
                       f"但 {'、'.join(missing)}")
    return out


# ---------------------------------------------------------------- 承诺挨饿

def build_promises(*, outline: str, title: str = "",
                   ask: Callable[[str], str]) -> List[Dict[str, Any]]:
    """总纲对读者做出的、必须持续兑现的承诺。

    排纲一排几百章，总纲只有第一批被完整读过，后面靠摘要传递，
    承诺元素会悄悄饿死 —— 实测主角的功法写在总纲里，
    第 125 章之后 200 多章再没出现过。
    """
    if not (outline or "").strip():
        return []
    prompt = (
        f"下面是长篇作品《{title}》的总纲。提取其中**对读者做出的、"
        f"必须在正文里持续兑现的承诺**，四类：\n"
        f"- 成长线：主角的实力/地位/技艺怎么一级一级长起来\n"
        f"- 核心道具：反复起作用的信物、账本、兵器、法宝\n"
        f"- 核心关系：未了的恩怨与情感债\n"
        f"- 终局条件：结尾必须达成的事\n\n"
        f"每条都要给出**兑现判据 done_when**：怎样才算这条承诺真的兑现了？\n"
        f"必须具体到能一眼验证的**事件** —— 「主角变强了」不算，"
        f"「在公平较量里打赢某某」才算；「那样东西起了作用」不算，"
        f"「用它救下某人／换到某物」才算。\n"
        f"只提最重要的 8-12 条。只输出 JSON，不要代码围栏：\n"
        '{"promises":[{"kind":"成长线","text":"一句话",'
        '"done_when":"具体到可验证的事件",'
        '"keywords":["用于粗筛的关键词","别名"]}]}\n\n'
        f"{clip(outline, 14000, '总纲(承诺清单)')}")
    data = parse_json(ask(prompt), "promises")
    out = []
    for i, p in enumerate((data.get("promises") or [])[:14]):
        if not isinstance(p, dict) or not str(p.get("text") or "").strip():
            continue
        out.append({
            "id": i + 1,
            "kind": str(p.get("kind") or "")[:8],
            "text": str(p["text"])[:120],
            # 兑现判据。只记「推进到第几章」是不够的 —— 实测某书铁律写明
            # 「一百二十发必须有被打出去的时候」，进了承诺清单，巡检每批都报
            # 「推进了」（因为点验一次弹单也算推进），全书 346 章一发没开。
            # 「推进」与「兑现」是两回事，必须分开记。
            "done_when": str(p.get("done_when") or "")[:120],
            "done_at": 0,
            "keywords": [str(x)[:12] for x in (p.get("keywords") or [])][:6],
            "last_advanced": 0,
        })
    return out


def due_at(p: Dict[str, Any]) -> int:
    """这条承诺**最早该开始动**的章号 —— 判据里写明了就按判据。

    没到点的事不算欠账。实测第 105 章时报「玉佩这条已饿 95 章」, 可它的
    兑现判据白纸黑字写着「第252章赵若锦被救时…第335章凭此证明身份」——
    人还没登场就催债, 模型不理它是对的, 而这种误报会挤掉真正的欠账。
    """
    nums = [int(x) for x in re.findall(r"第(\d{1,4})章",
                                       str(p.get("done_when") or ""))]
    return min(nums) if nums else 0


def starving(promises: Sequence[Dict[str, Any]], upto: int,
             gap: int = 60) -> List[str]:
    """已经 gap 章没被推进过的承诺（没到点的、以及铁律，都不算）。

    **铁律必须排除**。铁律是被强制塞进承诺清单的常驻约束(见 promises()),
    塞进来是为了让 unfulfilled() 能查「这件事到底发生过没有」—— 那是对的。
    可「最近推进过没有」对常驻约束根本不成立: 「每一章都要有一个当场兑现的
    小胜」不存在推进一次就打勾, last_advanced 永远停在 0/40, 于是**每一批
    都报挨饿**。而纠偏单只有 3 个名额, 实测 131-140 那批三个名额全被铁律
    占满(「承诺挨饿；承诺挨饿；承诺挨饿」), 真正挨饿的承诺一条都露不出来。
    噪声检测器比没有更糟。
    """
    out = []
    for p in promises or []:
        if p.get("kind") == "铁律":
            continue
        last = int(p.get("last_advanced") or 0)
        if upto < due_at(p):
            continue
        if upto - last >= gap:
            out.append(f"[{p.get('kind','')}] {p['text']}"
                       f"（末次推进第 {last} 章，已饿 {upto - last} 章）")
    return out


def unfulfilled(promises: Sequence[Dict[str, Any]], upto: int,
                total: int = 0) -> List[str]:
    """有兑现判据、却始终没兑现的承诺。

    与「挨饿」不同：挨饿看的是久没推进，这里看的是**到底有没有发生过那件事**。
    一条承诺可以章章都在推进，却一次都没兑现 —— 那把从第一章挂到最后一章、
    一发没开的枪就是这么来的。
    """
    out = []
    for p in promises or []:
        dw = str(p.get("done_when") or "").strip()
        if not dw or p.get("done_at"):
            continue
        # 全书快走完了才报，中途没兑现是正常的
        if total and upto < total * 0.75:
            continue
        out.append(f"[{p.get('kind','')}] {str(p.get('text',''))[:50]}"
                   f" —— 判据「{dw[:60]}」至今没发生过")
    return out


def promise_brief(promises: Sequence[Dict[str, Any]], upto: int,
                  gap: int = 60) -> str:
    hungry = starving(promises, upto, gap)
    if not hungry:
        return ""
    return ("【总纲承诺已久未兑现，本批必须推进其中至少一条】\n"
            + "\n".join(f"- {h}" for h in hungry[:5]))


# ---------------------------------------------------------------- 三条阶梯

#: 三条随全书进度演进的线。槽位通用，内容由题材包给默认、开书时按本书实例化。
#:
#: 为什么要有它：题材包里的 power 槽原来只有「记账口径」（境界怎么写），
#: 没有「第几章该到第几级」，也没人检查 —— 实测某书主角的横练功夫写在总纲里，
#: 第 125 章之后 200 多章再没出现过。爽点同理：pleasureBeats 固定四拍，
#: 346 章一个配方，读到后面必然疲。
LADDER_KINDS: List[Dict[str, str]] = [
    {"key": "power", "label": "力量线",
     "hint": "主角的实力/地位/技艺怎么一级一级长起来。每一级要能被验证"
             "（打得过谁、管得了多少人、进得了哪扇门）"},
    {"key": "pleasure", "label": "爽点配方",
     "hint": "这一段靠什么让读者爽。前期与后期不该是同一种爽 —— "
             "以小博大 / 以势压人 / 定规矩，是三种不同的配方"},
    {"key": "persona", "label": "主角特质",
     "hint": "主角这一段最突出的是什么本事与什么毛病。人是会变的，"
             "开局的谨慎到后期该变成别的东西"},
]


def build_ladders(*, outline: str, total_chapters: int, title: str = "",
                  genre: Optional[Dict[str, Any]] = None,
                  extra_kinds: Optional[Sequence[Dict[str, str]]] = None,
                  ask: Callable[[str], str]) -> Dict[str, List[Dict[str, Any]]]:
    """按本书总纲实例化三条阶梯。

    题材包只给默认口径（修仙填境界、电竞填段位、宫斗填位分、军事填军衔），
    具体每一级是什么、什么时候到，由模型读总纲决定。
    """
    if not (outline or "").strip():
        return {}
    spec = (genre or {}).get("ledgers") or {}
    power_hint = ((spec.get("power") or {}).get("hint") or "").strip()
    # 额外的线：题材包的 power 槽常把两件事塞在一起（「战力与身份」），
    # 模型只会挑一半去排 —— 实测某书 8 级阶梯里 5 级是商业地位，
    # 武功线排到第 3 级就停了，而武功恰恰是本书点名要兑现的成长线。
    # 所以点名的线要能**独立成一条**，不跟别人挤一个槽。
    all_kinds = list(LADDER_KINDS) + list(extra_kinds or [])
    kinds = "\n".join(f"- {k['label']}（{k['key']}）：{k['hint']}"
                      + (f"\n  本题材的口径：{power_hint}"
                         if k["key"] == "power" and power_hint else "")
                      for k in all_kinds)
    prompt = (
        f"下面是长篇作品《{title}》的总纲，全书 {total_chapters} 章。\n\n"
        f"为它排三条**随进度演进的阶梯**：\n{kinds}\n\n"
        f"每条 4-7 级。每级给出：这一级是什么、到第几章应该达到、"
        f"怎么验证已经到了。\n"
        f"级与级之间必须**看得出差别** —— 「变强了」不算，"
        f"「从接二流二十招到接一流三十招」才算。\n\n"
        f"只输出 JSON，不要代码围栏：\n"
        '{"power":[{"stage":"这一级是什么","by":50,"check":"怎么验证"}],'
        '"pleasure":[...],"persona":[...]}\n'
        f"（上面列出的每一条线都要给，一条都不能省）\n"
        f"by 是章号（1-{total_chapters}），必须递增。\n\n"
        f"#总纲\n{clip(outline, 12000, '总纲(支线)')}")
    data = parse_json(ask(prompt))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for k in (x["key"] for x in all_kinds):
        rungs = []
        for r in (data.get(k) or [])[:8]:
            if not isinstance(r, dict) or not str(r.get("stage") or "").strip():
                continue
            try:
                by = int(r.get("by") or 0)
            except (TypeError, ValueError):
                continue
            rungs.append({"stage": str(r["stage"])[:80], "by": max(1, by),
                          "check": str(r.get("check") or "")[:80], "reached": 0})
        rungs.sort(key=lambda x: x["by"])
        if rungs:
            out[k] = rungs
    return out


def ladder_rung(rungs: Sequence[Dict[str, Any]], n: int) -> Optional[Dict[str, Any]]:
    """第 n 章按计划应该处在哪一级。"""
    cur = None
    for r in rungs or []:
        if n >= r["by"]:
            cur = r
        else:
            break
    return cur or (rungs[0] if rungs else None)


def ladder_next(rungs: Sequence[Dict[str, Any]], n: int) -> Optional[Dict[str, Any]]:
    for r in rungs or []:
        if r["by"] > n:
            return r
    return None


def ladder_brief(ladders: Dict[str, List[Dict[str, Any]]], n: int,
                 extra_kinds: Optional[Sequence[Dict[str, str]]] = None) -> str:
    """注入排纲/写作的阶梯约束块。"""
    if not ladders:
        return ""
    lab = {k["key"]: k["label"] for k in list(LADDER_KINDS) + list(extra_kinds or [])}
    lines = ["【三条线当前该走到哪一步（按全书进度，不许原地踏步）】"]
    for key, rungs in ladders.items():
        cur, nxt = ladder_rung(rungs, n), ladder_next(rungs, n)
        if not cur:
            continue
        seg = f"- {lab.get(key, key)}：现在应处于「{cur['stage']}」"
        if cur.get("check"):
            seg += f"（验证：{cur['check']}）"
        if nxt:
            seg += f"；第 {nxt['by']} 章前要迈到「{nxt['stage']}」"
        lines.append(seg)
    return "\n".join(lines) if len(lines) > 1 else ""


def ladder_stalled(ladders: Dict[str, List[Dict[str, Any]]], n: int,
                   gap: int = 60) -> List[str]:
    """某条线已经很久没有推进过 —— 实测力量线断在第 125 章，之后 200 多章没动。"""
    lab = {k["key"]: k["label"] for k in LADDER_KINDS}
    out = []
    for key, rungs in (ladders or {}).items():
        last = max([r.get("reached") or 0 for r in rungs] or [0])
        if not last:
            # 一次都没记录过 ≠ 停滞。巡检是后加的，新书前几批也还没记账，
            # 这时报「已停 N 章」是拿「没有证据」当「有反证」——
            # 每本书跑到第 gap 章就三条线一起误报。
            # 「是不是落后于计划」由 ladder_brief 每批讲，不由停滞检测来讲。
            continue
        if n - last >= gap:
            cur = ladder_rung(rungs, n)
            out.append(f"{lab.get(key, key)}：末次推进第 {last} 章，已停 {n - last} 章"
                       f"（当前应在「{(cur or {}).get('stage', '?')}」）")
    return out


# ---------------------------------------------------------------- 支线

#: 支线做成一等公民。
#:
#: 为什么：原来只有主线（阶段骨架的 goal→steps 链）有结构，支线只在承诺清单里
#: 以一句话存在 —— 没有起止、没有归属人、没有交织节奏。实测一部水浒同人里，
#: 「梁山线」从来就不是一条线，只是一句承诺，于是 108 将除两人外全部零出场，
#: 「梁山」二字在 328 章里只出现 15 次，第 158 章后彻底消失。
#:
#: 主线管方向，支线管密度。全书只有一条线在走，就是「干巴」的根子。
def build_threads(*, outline: str, stages: Sequence[Dict[str, Any]],
                  total_chapters: int, title: str = "",
                  roster: Optional[Sequence[str]] = None,
                  ask: Callable[[str], str]) -> List[Dict[str, Any]]:
    """从总纲与阶段骨架里读出支线。"""
    if not (outline or "").strip():
        return []
    stage_line = "；".join(f"{s['name']}(第{s['start']}-{s['end']}章)"
                          for s in (stages or []))
    prompt = (
        f"下面是长篇作品《{title}》的总纲。全书 {total_chapters} 章"
        + (f"，阶段划分：{stage_line}" if stage_line else "") + "。\n\n"
        f"主线是主角一级级往上走的那条。请把**支线**单独列出来 —— "
        f"与主线交织、但有自己的起止与归宿的线：\n"
        f"- 人物线：某个重要配角自己的命运（他的目标、他的代价、他的结局）\n"
        f"- 势力线：某个组织/门派/阵营的兴衰\n"
        f"- 情感线：未了的恩怨与情债\n"
        f"- 谜团线：一个悬念从埋下到揭开\n\n"
        f"列 4-8 条。每条要有：名字、类型、归属的人（或组织）、"
        f"从第几章到第几章、这条线的 3-6 个关键节点、"
        f"以及**多少章至少要露一次面**（cadence：线越重要数越小；"
        f"贯穿全书的主要支线 10-15，阶段性的 20-30）。\n\n"
        # 支线不是一条线, 是【一个人 + 他手里的一张牌】。逐章扫描原作人名分布
        # 得到的是「大簇+长空白」(秦桧最大空白 183 章、洪承畴 781 章、王承恩 1047 章,
        # 一回来就是连续 8~72 章的密集簇), 与 cadence 这种均匀节拍器正相反。
        # 真机制是牌价随局势浮动, 牌一变值钱他自己就冒出来。
        f"另外，每条线还要写清三件事 —— 支线不是一张待办清单，"
        f"是**一个人手里攥着一张牌**：\n"
        f"- card：他手里的那张牌（一个把柄／一笔债／一个身份／一门手艺／一支队伍）\n"
        f"- valuable_when：什么局面下这张牌**突然变值钱**（2-3 条。"
        f"这是他被自动召唤的条件，不是日历）\n"
        f"- leverage：他和主角之间的**双向把柄**（我捏着你的，你也捏着我的）。"
        f"单向的迟早被清算掉，双向的能撑几百章\n\n"
        # 支线的收尾同样要守张力铁律。**这条原先只写进了张力提示词**, 于是支线
        # 里冒出「转身离去，血债以时间销账」「以大局退却」这种写法 —— 张力账
        # 那边刚把这个人写成「不死不休、禁止妥协」, 支线这边让他自己走了,
        # 两份资产打架, 而细纲是照着支线拍子写的。
        f"**每条线的 ending 与最后一个节点必须是明写的事件**：了结、"
        f"以代价换暂压、转移到第三方、或双方都付出代价的爆发。\n"
        f"不许写「时间冲淡」「转身离去」「以大局为重而退却」「不了了之」"
        f"这类靠淡出收场的写法 —— 那不是收尾，是把账赖掉。\n\n"
        f"只输出 JSON，不要代码围栏：\n"
        '{"threads":[{"name":"某某线","kind":"势力","owner":["甲","乙"],'
        '"org":"某组织或空字符串","span":[30,235],"cadence":12,'
        '"card":"他手里的那张牌","valuable_when":["什么局面下它值钱"],'
        '"leverage":"双向把柄",'
        '"beats":["节点1","节点2"],"ending":"这条线最后怎么收"}]}\n\n'
        f"可用角色：{'、'.join(roster or []) or '（见总纲）'}\n\n"
        f"#总纲\n{clip(outline, 12000, '总纲(支线)')}")
    data = parse_json(ask(prompt), "threads")
    out = []
    for i, x in enumerate((data.get("threads") or [])[:10]):
        if not isinstance(x, dict) or not str(x.get("name") or "").strip():
            continue
        span = x.get("span") or []
        try:
            a, b = int(span[0]), int(span[1])
        except (TypeError, ValueError, IndexError):
            a, b = 1, total_chapters
        try:
            cad = int(x.get("cadence") or 20)
        except (TypeError, ValueError):
            cad = 20
        out.append({
            "id": i + 1,
            "name": str(x["name"])[:24],
            "kind": str(x.get("kind") or "")[:8],
            "owner": [canon_name(o) for o in (x.get("owner") or []) if str(o).strip()][:5],
            "org": str(x.get("org") or "")[:24],
            "span": [max(1, a), max(a, b)],
            "cadence": max(4, min(cad, 60)),
            "beats": [str(z)[:50] for z in (x.get("beats") or [])][:8],
            "ending": str(x.get("ending") or "")[:120],
            "card": str(x.get("card") or "")[:90],
            "valuable_when": [str(z)[:60] for z in (x.get("valuable_when") or [])][:3],
            "leverage": str(x.get("leverage") or "")[:90],
            "last_touched": 0,
        })
    return out


def thread_owners(t: Dict[str, Any], protagonist: str = "",
                  aliases: Optional[Dict[str, str]] = None,
                  all_threads: Optional[Sequence[Dict[str, Any]]] = None) -> List[str]:
    """支线的**承载者** —— 用来判断这条线有没有动的那几个人。

    去掉两类人，去掉之后剩下的才有判别力：

    ① **主角**。每条支线都与主角有关，模型列 owner 时自然把他写进去，
       而他章章出场 —— 留着他，每条线都判定「露过面了」。
    ② **已经是别条线台柱的人**。实测「梁山账·生路名单」挂着武松，而武松是
       「武松·恩仇转军法」的头号归属人、全书 238 章有戏；于是梁山线断在
       第 158 章却报 ok。他在场只说明他自己那条线在走，不说明梁山在走。

    剩下鲁智深、林冲与「梁山」这个组织名，才是这条线真正的判据。
    """
    hero = canon_name(protagonist, aliases) if protagonist else ""
    # 别条线的头号归属人（owner 里第一个非主角的人）
    pillars = set()
    for x in all_threads or []:
        if x is t or x.get("id") == t.get("id"):
            continue
        for o in x.get("owner") or []:
            c = canon_name(o, aliases)
            if c and c != hero:
                pillars.add(c)
                break
    out = []
    for o in t.get("owner") or []:
        c = canon_name(o, aliases)
        if not c or (hero and c == hero) or c in pillars:
            continue
        out.append(c)
    # 全被剔光时退回原名单（去掉主角）—— 宁可判得松，也不能没有判据
    return out or [canon_name(o, aliases) for o in (t.get("owner") or [])
                   if not hero or canon_name(o, aliases) != hero]


def active_threads(threads: Sequence[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    return [t for t in threads or [] if t["span"][0] <= n <= t["span"][1]]


def thread_brief(threads: Sequence[Dict[str, Any]], n: int,
                 driver: str = "cadence") -> str:
    """注入排纲的支线约束块。

    带上「前几次是怎么露面的」，因为只要求「必须推进」会推出四章一个模子 ——
    实测某书的暗线每 12 章按时露面四次，四次全是同一套：主帅核账 → 部下催战
    → 主帅先问粮 → 行商带来南边闲话 → 下两道令 → 一句意味深长的话。
    节奏机制保证了「露面」，保证不了「露面方式不同」，得把上次的写法喂回去。
    """
    live = active_threads(threads, n)
    if not live:
        return ""
    if driver == "cards" and any(t.get("card") for t in live):
        return _thread_brief_cards(live, n)
    lines = ["【本批活着的支线（主线管方向，支线管密度 —— 全书只有一条线在走就会干）】"]
    for t in live[:8]:
        who = "、".join(t["owner"]) or t.get("org") or ""
        gap = n - (t.get("last_touched") or t["span"][0])
        due = "  ← 已超期，本批必须推进" if gap >= t["cadence"] else ""
        lines.append(f"- {t['name']}（{t['kind']}｜{who}｜第{t['span'][0]}-{t['span'][1]}章｜"
                     f"每 {t['cadence']} 章至少露一次，已隔 {gap} 章）{due}")
        if t.get("beats"):
            lines.append(f"    节点：{' → '.join(t['beats'])}")
        how = t.get("recent_how") or []
        if how:
            lines.append(f"    前几次这样露的面：{'；'.join(how[-3:])}")
            lines.append(f"    ⚠ 本批**必须换一种方式**推进它：换场景、换视角人物、"
                         f"换事件类型、换它与主线咬合的方式。重复上面的套路算不合格。")
    return "\n".join(lines)


def _thread_brief_cards(live: Sequence[Dict[str, Any]], n: int) -> str:
    """牌市版的支线块 —— 不按日历派活，按牌价召唤。

    实测原作里支线的分布是「大簇 + 长空白」：秦桧最大空白 183 章、洪承畴 781 章、
    王承恩 1047 章，而一回来就是连续 8~72 章的密集簇。cadence（每 N 章露一次）
    是均匀节拍器，与这个形状正相反 —— 按它写出来读者的感受是「这条线又来了」，
    而不是「他怎么来了」。

    真机制：每个人手里有一张牌，牌价随局势浮动，牌一变值钱他自己就冒出来。
    所以这里只把牌摊开，让排纲那一步自己判断谁该登场。
    """
    lines = ["【手里有牌的人（支线不是一张待办清单，是一群攥着牌的人）】"]
    for t in live[:8]:
        who = "、".join(t.get("owner") or []) or t.get("org") or t["name"]
        last = t.get("last_touched") or 0
        seen = f"上次露面第 {last} 章，已隔 {n - last} 章" if last else "还没露过面"
        lines.append(f"- {who}｜{t['name']}｜{seen}")
        if t.get("card"):
            lines.append(f"    牌：{t['card']}")
        if t.get("valuable_when"):
            lines.append(f"    什么时候值钱：{'；'.join(t['valuable_when'])}")
        if t.get("leverage"):
            lines.append(f"    双向把柄：{t['leverage']}")
        how = t.get("recent_how") or []
        if how:
            lines.append(f"    前几次这样露的面：{'；'.join(how[-2:])}"
                         f"　⚠ 再登场必须换一种方式")
    lines.append(
        "⚠ **不要按「谁很久没出现」来安排出场**。只问一句：按本批的局面，"
        "谁手上的牌**突然变值钱了**？值钱的才登场，而且要能一句话说清"
        "他为什么现在来；说不清就别来。\n"
        "　牌不值钱的人继续消失，多久都行 —— 不要给他「交代一句近况」，那是稀释。\n"
        "　如果有两个人的牌在同一件事上同时值钱，让他们撞上：写清他们必须合作的理由，"
        "和他们必然互相坑的理由。撞出来的东西是第三条线，不用另外设计。")
    return "\n".join(lines)


def thread_repetitive(threads: Sequence[Dict[str, Any]]) -> List[str]:
    """连着几次用同一种方式露面的支线 —— 按时出场了，但读起来是同一章。"""
    out = []
    for t in threads or []:
        how = [str(x) for x in (t.get("recent_how") or [])]
        if len(how) < 3:
            continue
        # 判重交给字面重合度: 三次描述里两两都高度相似才报, 单纯用词像不算
        def sim(a, b):
            sa, sb = set(a), set(b)
            return len(sa & sb) / max(1, len(sa | sb))
        pairs = [sim(how[-1], how[-2]), sim(how[-2], how[-3]), sim(how[-1], how[-3])]
        if sum(1 for x in pairs if x > 0.5) >= 2:
            out.append(f"{t['name']}：连着三次用同一套写法露面（{how[-1][:40]}…）")
    return out


def thread_overdue(threads: Sequence[Dict[str, Any]], n: int) -> List[str]:
    """超过自己节奏没露面的支线。"""
    out = []
    for t in active_threads(threads, n):
        gap = n - (t.get("last_touched") or t["span"][0])
        if gap >= t["cadence"]:
            out.append(f"{t['name']}（每 {t['cadence']} 章该露一次，已隔 {gap} 章）")
    return out


def thread_last_seen(threads: Sequence[Dict[str, Any]], outlines: Dict[str, str],
                     aliases: Optional[Dict[str, str]] = None,
                     protagonist: str = "") -> None:
    """拿已排好的细纲回填每条支线的末次露面 —— 就地改 threads。

    给「骨架是后加的、细纲已经排完」的书用：巡检只对之后的批次生效，
    之前排的那些得回放一遍才知道断在哪。
    判据是归属人真的出场，或组织名出现在细纲里 —— 只被提一句不算露面。

    **主角必须排除在归属人之外**：每条支线都与主角有关，模型列 owner 时
    自然会把主角写进去，而主角章章出场 —— 于是每条线都判定「露过面了」，
    检测器全绿。实测第一版就是这样：梁山线断在第 158 章，却报 ok。
    一个永远不报警的检测器比没有更糟。
    """
    app = cast_appearances(outlines, aliases=aliases)
    hero = canon_name(protagonist, aliases) if protagonist else ""
    nums = sorted(int(k) for k in outlines if str(k).isdigit())
    for t in threads or []:
        last = 0
        for o in thread_owners(t, protagonist, aliases, threads):
            cs = [c for c in (app.get(canon_name(o, aliases)) or [])
                  if t["span"][0] <= c <= t["span"][1]]
            if cs:
                last = max(last, cs[-1])
        if t.get("org"):
            for n in nums:
                if t["span"][0] <= n <= t["span"][1] and t["org"] in outlines[str(n)]:
                    last = max(last, n)
        t["last_touched"] = last


def promise_last_seen(promises: Sequence[Dict[str, Any]],
                      outlines: Dict[str, str]) -> None:
    """拿已排好的细纲回填每条承诺的末次推进 —— 就地改 promises。

    没有这一步，「没记录」会被当成「饿着」：巡检是后加的，之前排的章节
    一条记录都没有，于是每条承诺都报「已饿 346 章」，12 条误报全指向同样
    两章。检测器对所有东西都报警，和从不报警一样没用。

    关键词只做零成本预筛（关键词本身是模型生成的），判「提了一嘴还是真推进」
    留给巡检 —— 这里只求不把有记录的当成没有。
    """
    nums = sorted(int(k) for k in outlines if str(k).isdigit())
    for p in promises or []:
        kws = [w for w in ([p.get("text", "")[:6]] + list(p.get("keywords") or [])) if w]
        hits = [n for n in nums if any(w in outlines[str(n)] for w in kws)]
        if hits:
            p["last_advanced"] = max(int(p.get("last_advanced") or 0), hits[-1])


def merge_repairs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把落在同一批章节上的重排单合并成一条。

    不合并的话，同几章会被多条单子各重排一次，**后一次覆盖前一次** ——
    前面几次的修复全部作废，还白烧了几次生成。
    """
    bucket: Dict[tuple, Dict[str, Any]] = {}
    for j in jobs:
        key = tuple(j["chapters"])
        if key in bucket:
            bucket[key]["kind"] += f"＋{j['kind']}"
            bucket[key]["demands"].append(j["demand"])
        else:
            bucket[key] = {"kind": j["kind"], "chapters": list(key),
                           "demands": [j["demand"]]}
    out = []
    for b in bucket.values():
        ds = b.pop("demands")
        b["kind"] = "＋".join(dict.fromkeys(b["kind"].split("＋")))
        b["demand"] = ("这几章要同时解决下面几件事：\n"
                       + "\n".join(f"{i+1}) {d}" for i, d in enumerate(ds))
                       if len(ds) > 1 else ds[0])
        out.append(b)
    return sorted(out, key=lambda x: x["chapters"][0])


# ---------------------------------------------------------------- 解法谱系

#: 主角解决冲突的**手段类型**。七类对所有题材通用：修仙、都市、宫斗、
#: 军事、电竞、同人里的冲突，最终都落在这七种解法之一上。
#:
#: 为什么要管这个：长篇最隐蔽的疲劳不是「对手太弱」，而是**主角的招式一成
#: 不变**。实测某书七个对手轮换了一百多章，主角七次全是同一招（拿账顶回去）；
#: 对手一个比一个强、一个都不降智，读起来照样疲 —— 因为读者早就知道他会怎么赢。
#:
#: cadence 机制管的是「谁多久露一次面」，这里管的是「主角多久换一种赢法」。
RESOLUTION_MODES: List[Dict[str, str]] = [
    {"key": "outwit", "label": "智取",
     "hint": "靠信息差、推演、布局赢 —— 他比对手多知道一件事，或多算一层"},
    {"key": "force", "label": "力破",
     "hint": "正面硬碰赢 —— 武力、战力、兵力、执行力，不绕弯子"},
    {"key": "leverage", "label": "借势",
     "hint": "借第三方的力赢 —— 靠山、规则、舆论、敌人的敌人"},
    {"key": "trade", "label": "交易",
     "hint": "付代价换结果 —— 谈判、让利、割肉、拿自己的东西换"},
    {"key": "persuade", "label": "收心",
     "hint": "把人变成自己人 —— 说服、感化、结盟、以诚换诚"},
    {"key": "endure", "label": "忍退",
     "hint": "这一局不赢 —— 退让、示弱、蛰伏、认下损失换以后"},
    {"key": "upend", "label": "破局",
     "hint": "改变规则本身 —— 掀桌、另开一局、让原来的胜负标准失效"},
]

MODE_KEYS = [m["key"] for m in RESOLUTION_MODES]
_MODE_LABEL = {m["key"]: m["label"] for m in RESOLUTION_MODES}


def mode_menu() -> str:
    return "\n".join(f"- {m['label']}（{m['key']}）：{m['hint']}"
                     for m in RESOLUTION_MODES)


def mode_brief(recent: Sequence[str], n: int = 0) -> str:
    """注入排纲的解法约束块。

    `recent` 是最近几批用过的解法（每批一到三个），按批次先后排列。
    """
    if not recent:
        return ""
    tail = list(recent)[-9:]
    cnt: Dict[str, int] = {}
    for k in tail:
        cnt[k] = cnt.get(k, 0) + 1
    used = "、".join(f"{_MODE_LABEL.get(k, k)}×{v}"
                    for k, v in sorted(cnt.items(), key=lambda x: -x[1]))
    cold = [m["label"] for m in RESOLUTION_MODES if m["key"] not in cnt]
    lines = ["【主角最近几批的赢法（不许再用同一种赢下去）】",
             f"已用：{used}"]
    if cold:
        lines.append(f"久未用：{'、'.join(cold)}")
    lines.append("本批至少有一场关键冲突，**换一种没在上面高频出现的赢法**。"
                 "对手可以一个比一个强，但主角每次都用同一招，读者早就知道他会怎么赢。")
    lines.append("七种赢法：\n" + mode_menu())
    return "\n".join(lines)


def mode_monotony(recent: Sequence[str], window: int = 6,
                  ratio: float = 0.7) -> List[str]:
    """最近 window 批里某一种解法占比过高 —— 招式单一。"""
    tail = list(recent)[-window:]
    if len(tail) < window:
        return []
    cnt: Dict[str, int] = {}
    for k in tail:
        cnt[k] = cnt.get(k, 0) + 1
    out = []
    for k, v in cnt.items():
        if v / len(tail) >= ratio:
            out.append(f"最近 {len(tail)} 次关键冲突里有 {v} 次靠「"
                       f"{_MODE_LABEL.get(k, k)}」取胜，赢法太单一")
    return out


def mode_unused(recent: Sequence[str], min_history: int = 9,
                min_cold: int = 3) -> List[str]:
    """长期只在几种赢法里打转 —— 单一检测漏掉的那一半。

    mode_monotony 查的是「某一种占比过高」，可实测更常见的形状是
    **三种在循环、另外四种从没用过**：智取／交易／借势轮着来，占比都不高，
    单一检测一条都不报，读起来照样是同一个人在用同一套本事。
    """
    hist = [k for k in (recent or []) if k in MODE_KEYS]
    if len(hist) < min_history:
        return []
    cold = [m["label"] for m in RESOLUTION_MODES if m["key"] not in set(hist)]
    if len(cold) < min_cold:
        return []
    hot = "、".join(_MODE_LABEL[k] for k in dict.fromkeys(hist))
    return [f"最近 {len(hist)} 次关键冲突只在「{hot}」里打转，"
            f"「{'、'.join(cold)}」一次没用过"]


# ---------------------------------------------------------------- 挫败配额

def setback_brief(stage: Optional[Dict[str, Any]], setbacks: Sequence[Dict[str, Any]],
                  n: int, quota: int = 1) -> str:
    """本阶段的挫败配额还差几次。

    爽文也需要主角栽跟头 —— 不是为了虐，是因为**从不失手的人不值得担心**。
    实测某书中段 27 章全是「查—证—记档」，主角一次没输过，对手一个比一个强
    也救不回来：读者不担心，就不往下翻。

    判据比「有没有挫折」严一层：必须是**主角自己判断错**、**付出不可逆的
    代价**、而且**不是靠他的看家本领翻盘**。被对手打了一下又立刻用老办法赢
    回来，那不叫挫败，那是爽点的前摇。
    """
    if not stage:
        return ""
    a, b = stage["start"], stage["end"]
    got = [s for s in (setbacks or []) if a <= int(s.get("ch") or 0) <= b]
    if len(got) >= quota:
        return ""
    span = max(1, b - a + 1)
    left = b - n
    urgent = left <= span * 0.35
    lines = [f"【本阶段的挫败配额：需 {quota} 次，已有 {len(got)} 次】"]
    if got:
        lines.append("已有：" + "；".join(f"第{s.get('ch')}章 {str(s.get('what'))[:40]}"
                                        for s in got[:3]))
    lines.append("这一阶段必须有一次**主角自己判断错**、**付出收不回来的代价**、"
                 "而且**不是靠他的看家本领翻盘**的失手。"
                 "被打一下又立刻用老办法赢回来不算 —— 那是爽点的前摇，不是挫败。")
    if urgent:
        lines.append(f"⚠ 本阶段只剩 {max(0, left)} 章，这一次必须落在本批。")
    return "\n".join(lines)


def setback_missing(stages: Sequence[Dict[str, Any]],
                    setbacks: Sequence[Dict[str, Any]],
                    upto: int, quota: int = 1) -> List[str]:
    """已经走完、却没凑够挫败配额的阶段。"""
    out = []
    for s in stages or []:
        if s["end"] > upto:
            continue
        got = [x for x in (setbacks or [])
               if s["start"] <= int(x.get("ch") or 0) <= s["end"]]
        if len(got) < quota:
            out.append(f"{s['name']}（第{s['start']}-{s['end']}章）"
                       f"全程没有主角真正的失手，{len(got)}/{quota}")
    return out

# ─────────────────────── 世界自转（扩散因子） ───────────────────────
#
# 前面那些机制(误读/牌市/但是链)都挂在主角身上 —— 主角不动, 世界就不动。
# 而原作里最耐读的一部分恰恰是「主角不在场时世界自己在走」: 完颜宗构党争、
# 大金父慈子孝、赵构在金陵一口气取四百武进士、耶律大石西迁称帝……
# 主角回头一看, 世界变了。这是横向加宽的唯一来源。
#
# 做法: 给每个势力一份自己的目标与内部矛盾, 每隔几章让它们**各自走一步**,
# 完全不管主角在干什么。走出来的结果再当作下一批排纲的输入。

def build_factions(outline: str, roster: Sequence[str], title: str,
                   ask: Callable[[str], str], seed: str = "",
                   exist: Sequence[str] = (), arena: str = "",
                   want: int = 7, kinds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """从种子和总纲里抽出「会自己往前走」的势力。

    两条实测教训:
    1. **必须锚定种子点名的势力**。不给约束时模型会造「太清宗/万妖会/百鬼行」
       这些种子里没有的名字, 而种子写着青霄派/太虚院/紫薇阁/狐族/蛇族/炼尸/
       养蛊/鬼修/六圣 —— 和里程碑跑偏是同一个病: 种子里有, 但没当硬约束。
    2. **势力要随剧情推进生长**, 不是开书时定死 6 家就一辈子 6 家。主角走到
       新地盘(相国寺→商路→罗刹海→缥缈阁), 那一片的势力才该登场。
    """
    seed_line = ""
    if seed.strip():
        seed_line = (f"\n【种子里点名的势力（**优先用这些名字，不许另造同类新名**）】\n"
                     f"{seed.strip()}\n")
    exist_line = ""
    if exist:
        exist_line = (f"\n【已经建好的势力（不要重复，这次只补新的）】\n"
                      f"{'、'.join(exist)}\n")
    arena_line = (f"\n【当前故事走到了这里，优先补这一片的势力】\n{arena.strip()}\n"
                  if arena.strip() else "")
    prompt = (
        f"下面是长篇作品《{title}》的总纲。\n\n"
        + seed_line + exist_line + arena_line +
        + (f"\n【这个题材里的「{(kinds or {}).get('称呼','势力')}」通常有这几类，按需挑】\n"
           f"{'、'.join((kinds or {}).get('候选类型') or [])}\n" if kinds else "")
        + f"\n请列出 {want} 个**非主角{(kinds or {}).get('称呼','势力')}**。\n"
        f"关键要求：这些势力必须是**主角不在场时也会自己往前走**的东西，"
        f"不是等着主角来推的背景板。\n\n"
        f"每个势力给出：\n"
        f"- name 名称\n"
        f"- wants 它最想要什么（一句话，具体到可以据此行动）\n"
        f"- inner 它内部谁和谁在争、争什么（**这一栏最重要**：没有内部矛盾的势力"
        f"只会做理性选择，而理性的势力是不会犯错的，也就不会产生剧情）\n"
        f"- fears 它怕什么\n"
        f"- state 它现在的实力/处境（可数：多少人、占几处、握着什么）\n"
        f"- reads_hero 它此刻怎么看主角（多半是错的）\n\n"
        f"硬要求：至少有两个势力之间有**与主角无关**的直接利害冲突。\n\n"
        f'只输出 JSON，不要代码围栏：\n'
        f'{{"factions":[{{"name":"某派","wants":"…","inner":"…","fears":"…",'
        f'"state":"…","reads_hero":"…"}}]}}\n\n'
        f"可用角色：{'、'.join(roster or []) or '（见总纲）'}\n\n#总纲\n{clip(outline, 12000, '总纲(势力)')}")
    data = parse_json(ask(prompt), "factions")
    out = []
    for i, x in enumerate((data.get("factions") or [])[:8]):
        if not isinstance(x, dict) or not str(x.get("name") or "").strip():
            continue
        out.append({
            "id": i + 1,
            "name": str(x["name"])[:20],
            "wants": str(x.get("wants") or "")[:90],
            "inner": str(x.get("inner") or "")[:120],
            "fears": str(x.get("fears") or "")[:90],
            "state": str(x.get("state") or "")[:120],
            "reads_hero": str(x.get("reads_hero") or "")[:90],
            "last_turn": 0,
        })
    return out


def world_turn_prompt(factions: Sequence[Dict[str, Any]], n: int,
                      clock: str = "", elapsed: str = "") -> str:
    """让各势力各走一步 —— 主角不在场。"""
    lines = []
    for f in factions:
        lines.append(f"【{f['name']}】想要：{f['wants']}")
        if f.get("inner"):
            lines.append(f"　　内部在争：{f['inner']}")
        if f.get("fears"):
            lines.append(f"　　怕：{f['fears']}")
        lines.append(f"　　现状：{f.get('state','')}")
        if f.get("reads_hero"):
            lines.append(f"　　它眼里的主角：{f['reads_hero']}")
    return (
        "这是一次【世界回合】。**主角不在场，通篇不许提到主角在做什么。**\n\n"
        + "\n".join(lines)
        + (f"\n\n时钟：{clock}" if clock else "")
        + (f"\n距上一次世界回合过去了：{elapsed}" if elapsed else "")
        + f"\n\n让上面每一个势力，按**它自己的目标和它自己的内部矛盾**，各自往前走一步。\n"
          f"每个势力输出四行：\n"
          f"  做了什么：一个具体动作（不许写「继续发展」「暗中积蓄」这种空话）\n"
          f"  账变成了多少：它自己的实力/地盘/人手/筹码变成了什么\n"
          f"  没看见的隐患：这个动作在它自己看来是聪明的，但埋下了什么它没意识到的麻烦\n"
          f"  对主角的影响：这件事会怎么波及主角（可以是「暂时无关」）\n\n"
          f"硬要求：\n"
          f"- 至少一个势力做出**对它自己不利**的动作，因为内部斗争压倒了外部理性\n"
          f"- 至少一个势力因为**误判主角**而行动\n"
          f"- 不许所有势力都在针对主角。它们大部分时间在互相咬\n"
          f"- 至少有两个势力的动作**直接撞在一起**\n\n"
          f"最后单独一行「本回合最大的变化：」写清哪一条对天下格局影响最大。")


# ─────────────────────────── 作废与换壳 ───────────────────────────
# 五个发散算子里最后两个。误读(一变多)、代价(一变二)、自转(横向)已经在跑，
# 这两个管的是**纵向**：让时间真的在主角身上留下痕迹，而不是能力一路叠加。

def obsolete_prompt(method: str, uses: Sequence[Dict[str, Any]], n: int) -> str:
    """作废令：主角反复奏效的那一招，这一批要当众失灵一次。

    长篇写到中期原地打转，根子不是「敌人不够强」，是**主角的解法永远有效**。
    只要一招还灵，作者就没有理由让他学新的。所以要主动把它作废掉。
    """
    hist = "\n".join(f"　第{u['at']}章：{u.get('solved') or '解决了当时的麻烦'}"
                     for u in uses)
    return (
        f"【作废令·第 {n} 批细纲必须执行】\n"
        f"主角这一路数已经连着奏效 {len(uses)} 次了：\n"
        f"　招数：{method}\n{hist}\n\n"
        f"从这一批开始，**这一招要当众失灵一次**。要求：\n"
        f"- 失灵的原因必须是**前面几次奏效本身带来的**：有人见过了、有人研究过了、"
        f"有人专门针对它做了准备、或者当初奏效的那个条件已经被主角自己改掉了。"
        f"不许写成「遇到了更强的敌人」。\n"
        f"- 失灵要发生在**有人看着的场合**，让主角当场丢脸或吃亏，不许私下失败。\n"
        f"- 失灵之后主角**不许靠加强这一招过关**（更用力、更熟练、更高层次都不算）。"
        f"他必须临时用一个和这一招无关的东西换命。\n"
        f"- 把这一次失灵写进某一章的「代价」栏。\n"
        f"这不是削弱主角，是逼他长出第二条腿。")


def reshell_prompt(vol: Dict[str, Any], shell: str, left: int, n: int) -> str:
    """换壳令：卷末收走主角借来的那个身份/位置。"""
    return (
        f"【换壳令·本卷还剩 {left} 章】\n"
        f"主角现在站着的这个位置是：{shell or '（还没登记，按本卷主线里他现在的身份写）'}\n"
        f"本卷要解决的是：{vol.get('solves') or vol.get('name', '')}\n"
        f"本卷解法会暴露的是：{vol.get('exposes') or '（见分卷表）'}\n\n"
        f"卷末必须做到：**这个位置被收走或者作废**。要求：\n"
        f"- 收走的理由要长在「解决之后暴露」那一栏上 —— 是他自己解决问题的方式"
        f"把这个位置弄没的，不是外人无缘无故来抢。\n"
        f"- 收走之后，他**从这个位置拿到的好处要一并失效**（人脉、名分、通行的方便、"
        f"别人对他的忌惮）。只保留他身上拿不走的东西。\n"
        f"- 下一卷他必须换一个**性质不同**的新立足点，不许是同一个位置升一级"
        f"（例如从杂役升执事不算换壳，从宗门跑去做商队护卫才算）。\n"
        f"- 换壳这件事要有人**看错**：至少一方以为他是被赶走的丧家犬，"
        f"因此对他做出错误的动作。")


def clip(text: str, limit: int, what: str) -> str:
    """带记账的截断 —— 切了就吼。

    截断本身不是病，**静默截断**才是: 上限不触发时它没有代价, 一旦触发就
    悄悄丢掉信息, 而下游拿到的东西看起来是完整的。今天已经因为这类静默故障
    吃过六次亏(评审丢半把尺子、细纲空壳、搜索三千次 403 ……)。
    该压缩的压缩, 但压了必须留痕。
    """
    t = text or ""
    if len(t) <= limit:
        return t
    print(f"  [clip] {what}: {len(t)} → {limit} 字（切掉 {len(t)-limit}）", flush=True)
    return t[:limit]


def sc_title_examples(style_pack) -> str:
    """标题重起时给模型看的口味样例——取功能类各一例, 短。"""
    tl = (style_pack or {}).get("章标题模板库") or {}
    fc = tl.get("功能类") or []
    exs = []
    for t in fc[:6]:
        e = (t.get("例") or [None])[0]
        if e:
            exs.append(str(e))
    if not exs:
        return "口味：像说书人在喊话，念出来能听出是谁在开口；多点对手的名字。"
    return ("口味（各功能类一例，换着来）：" + "｜".join(exs)
            + "\n念出来必须能听出是谁在开口；目录里对手的名字要比主角多。")

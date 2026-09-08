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

def parse_json(text: str, want: str = "") -> Dict[str, Any]:
    """从模型输出里抠出第一个合法 JSON 对象。

    括号配平逐个试解析 —— 模型爱在 JSON 前后加解释、加代码围栏、
    甚至在 JSON 之后再补一段说明，直接 json.loads 十次有三次挂。
    """
    raw = re.sub(r"^```[a-z]*\s*|\s*```$", "", (text or "").strip(), flags=re.M)
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
        if isinstance(cand, dict) and (not want or want in cand):
            return cand
    return {}


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
        for nm in re.split(r"[、,，/｜|]", m.group(1)):
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
        f"#总纲\n{outline[:14000]}")
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
        f"{src[:12000]}")
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
        missing = []
        for raw in t.get("between") or []:
            nm = canon_name(raw, aliases)
            cs = appearances.get(nm) or []
            last = cs[-1] if cs else 0
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
        f"只提最重要的 8-12 条。只输出 JSON，不要代码围栏：\n"
        '{"promises":[{"kind":"成长线","text":"一句话",'
        '"keywords":["用于粗筛的关键词","别名"]}]}\n\n'
        f"{outline[:14000]}")
    data = parse_json(ask(prompt), "promises")
    out = []
    for i, p in enumerate((data.get("promises") or [])[:14]):
        if not isinstance(p, dict) or not str(p.get("text") or "").strip():
            continue
        out.append({
            "id": i + 1,
            "kind": str(p.get("kind") or "")[:8],
            "text": str(p["text"])[:120],
            "keywords": [str(x)[:12] for x in (p.get("keywords") or [])][:6],
            "last_advanced": 0,
        })
    return out


def starving(promises: Sequence[Dict[str, Any]], upto: int,
             gap: int = 60) -> List[str]:
    """已经 gap 章没被推进过的承诺。"""
    out = []
    for p in promises or []:
        last = int(p.get("last_advanced") or 0)
        if upto - last >= gap:
            out.append(f"[{p.get('kind','')}] {p['text']}"
                       f"（末次推进第 {last} 章，已饿 {upto - last} 章）")
    return out


def promise_brief(promises: Sequence[Dict[str, Any]], upto: int,
                  gap: int = 60) -> str:
    hungry = starving(promises, upto, gap)
    if not hungry:
        return ""
    return ("【总纲承诺已久未兑现，本批必须推进其中至少一条】\n"
            + "\n".join(f"- {h}" for h in hungry[:5]))

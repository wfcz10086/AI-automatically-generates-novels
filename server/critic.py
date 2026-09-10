"""逐章评审 —— 统计指标测不出来的问题，必须让模型真读。

人工抽读 5 章就发现了三个统计查不出的毛病：
  * 「灵魂里的林远冷静计算着数据」这类叙述拐杖，全书用了 791 次
  * 主角从商人变成知府，全书 0 处交代
  * 耶律休第 20 章自焚身亡，第 21 章起继续活动到第 177 章

第三条是最要命的：**不可逆事实没有台账**。角色状态机记的是「所在地/身体状态」，
会被后续覆盖；而「某人死了」「某人升了官」「某人叛变了」这类事实一旦确立就
不能推翻，必须单独锁住并作为硬约束注入。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

# 多遍阅读 —— 一遍读不出所有问题（人读也一样）。每遍换一个焦点:
#   第 1 遍逻辑读: 盯剧情、人物、设定、事实
#   第 2 遍文字读: 盯文风、套路、描写密度、开篇与钩子
#   第 3 遍衔接读(可选): 盯与上一章的承接、称谓与时间连续性
PASSES = [
    {"name": "逻辑读", "dims": [
        ("人物一致性", "人物的性格、能力、称谓、动机是否与前文一致；有没有临时变聪明或变蠢"),
        ("设定自洽", "世界观、官职、技术水平、时间线是否自洽；时代红线是否被踩；"
                    "资金/人数/资产等数值与前文是否连续，有无无由来的数量级跳变"),
        ("视角与人称", "叙述视角是否稳定；有没有让 A 角色知道只有 B 才知道的事"),
        ("剧情推进", "本章是否推进主线；有没有原地打转或重复前文"),
        # 光把「不许降智」写进提示词没用 —— 提示词只是要求, 没人查就等于没有。
        # 长篇里对手为了让主角赢而突然失能, 是最伤读者的一种塌陷, 而它不违反
        # 任何一条既有维度: 人物「一致地」蠢, 设定也自洽。
        ("对手智商守恒", "本章里与主角对立的一方，每一步是否都是**站在他的位置、"
                        "用他知道的信息**能做出的合理选择？他失败是否有具体原因"
                        "（信息差／代价算错／被自己人拖累／赌了小概率），"
                        "而不是「突然没想到」「莫名不设防」？主角赢是因为多算了一层，"
                        "还是因为对手临时变蠢？"),
        ("配角自主性", "有名有姓的配角是否各有自己想要的东西，且与主角不完全重合？"
                      "有没有人沦为只会惊叹、附和、复述主角的话的捧哏？"
                      "有没有人是接了命令就照办的执行器，从不拒绝、不还价、不办砸？"
                      "上一场的情绪有没有带到这一场，还是每章重置成中立？"),
    ]},
    {"name": "文字读", "dims": [
        ("文风新鲜度", "有没有反复使用同一叙述装置、同一比喻、同一钩子句式"),
        ("描写配给", "环境描写是否超配额；情绪形容词与比喻是否过密；有没有文学腔糊墙"),
        ("开篇与钩子", "第一句是否以对白/动作/事件开场；结尾钩子是否具体、是否与前几章雷同"),
        ("对白质感", "人物说话是否有各自腔调；有没有连续的说明式对白"),
    ]},
    {"name": "衔接读", "dims": [
        ("承接", "开头是否接住上一章的钩子；时间、地点、人物状态是否无缝衔接"),
        ("称谓连续", "对人物的称呼是否与前文一致；主角身份口径是否统一"),
    ]},
]
# real 模式（写真实朝代）专属维度。架空书不挂，挂了会把「本书自创的官职」
# 全判成错。实测缺口: 第 74 章主角说自己「在焚书坑儒时从泔水桶里捞出证据」——
# 秦朝的事安在洪武朝主角头上, 三遍评审全部放过, 因为没有任何一个维度问「史实」。
REAL_DIMS = [
    ("史实校验", "本章提到的历史事件、典故、人物、制度、器物、地名，是否属于本书"
                "所处的朝代？特别注意：角色声称亲历或亲见的事件必须发生在本朝且在"
                "当前时间点之前；他朝典故只能作为引用（「昔秦皇焚书」），不能写成"
                "亲历（「我在焚书坑儒时」）。真实历史人物的生卒、任职年份、亲属关系"
                "是否对得上"),
]

def style_dims(style_pack, pass_name: str):
    """文风包自己声明的评审维度。

    生成端换了一整套文风(结构件/字段契约/标题规格/窗口反馈), 评审端却还在用
    通用网文的那几条 —— 等于**用旧尺子量新东西**: 它不知道「当众失态」是必需的,
    不知道「制度解说是骨头」, 不知道叹号低说明人物在端着。
    评审不懂, 那条「不合格自动重写」的回路就没有方向, 重写出来的东西不会更靠近目标。

    包里按遍次声明: {"文字读": [["维度名","怎么判"], ...]}
    """
    d = (style_pack or {}).get("critiqueDims") or {}
    return [tuple(x) for x in (d.get(pass_name) or []) if len(x) == 2]


DIMENSIONS = PASSES[0]["dims"] + PASSES[1]["dims"]

CRITIQUE_SCHEMA = (
    '{"scores":{"人物一致性":85,"设定自洽":70,"视角与人称":90,'
    '"文风新鲜度":60,"剧情推进":80},'
    '"issues":[{"dim":"设定自洽","severity":"high|mid|low",'
    '"what":"一句话说清问题","evidence":"引用正文原句"}],'
    '"contradictions":[{"fact":"与哪条已确立事实冲突","evidence":"正文原句"}],'
    '"new_facts":[{"subject":"人物或事物","fact":"本章确立的不可逆事实",'
    '"kind":"death|rank|betray|marry|destroy|reveal|other"}],'
    '"tics":["本章出现的、属于套路的叙述装置或句式"]}'
)


def build_prompt(*, title: str, n: int, text: str, prev_texts: List[str],
                 world: str, roster: str, canon: List[Dict[str, Any]],
                 outline: str, budget_chars: int = 46000,
                 recalled: Optional[List[Dict[str, Any]]] = None,
                 digests: Optional[List[str]] = None,
                 roles: Optional[Dict[str, Any]] = None,
                 timeline: Optional[List[str]] = None,
                 dims_override: Optional[List[tuple]] = None,
                 pass_name: str = "",
                 real_mode: bool = False,
                 era_hint: str = "") -> str:
    """按预算装配评审上下文。

    64k 不是用来灌原文的 —— 灌原文只装得下四五章。走索引与压缩才能让
    评审「看见」全书：canon 是结构化事实（每条 30 字覆盖一个关键节点），
    L2/L3 摘要把几十章压成几百字，FTS5 只召回与本章真正相关的往期片段。
    """
    canon_lines = "\n".join(
        f"- 【{c.get('kind','other')}】{c.get('subject','')}：{c.get('fact','')}"
        f"（第{c.get('chapter','?')}章确立）" for c in canon[-80:])

    recall_lines = "\n".join(
        f"- [{h.get('kind','')}] {h.get('title','')}：{str(h.get('text',''))[:260]}"
        for h in (recalled or [])[:20])
    role_lines = "；".join(
        f"{k}（第{v.get('at')}章）{v.get('state','')}"
        for k, v in list((roles or {}).items())[:16])
    blocks: List[tuple] = [
        ("本章正文", text, 0),
        ("已确立的不可逆事实（本章不得推翻）", canon_lines, 1),
        ("本章细纲", outline, 2),
        ("角色最新状态（跨章跟踪）", role_lines, 3),
        ("与本章相关的往期片段（全书检索召回）", recall_lines, 4),
        ("角色档案", roster, 5),
        ("前情压缩摘要（每 10 章一段，覆盖全书）", "\n\n".join(digests or []), 6),
        ("时间线", "；".join(timeline or []), 7),
        ("世界观", world, 8),
    ]
    for i, pt in enumerate(reversed(prev_texts)):
        blocks.append((f"上一章正文（承接检查）", pt, 9 + i))

    used, parts = 0, []
    for name, body, _ in sorted(blocks, key=lambda x: x[2]):
        if not body:
            continue
        room = budget_chars - used
        if room < 500:
            break
        b = body if len(body) <= room else body[:room] + "\n…（截断）"
        parts.append(f"【{name}】\n{b}")
        used += len(b)

    dims_list = dims_override if dims_override else DIMENSIONS
    dims = "\n".join(f"  {i+1}. {d}：{desc}" for i, (d, desc) in enumerate(dims_list))
    return (
        (f"【本书为真实历史背景】{era_hint[:160]}\n"
         f"审读时把史实当硬约束：他朝的事件、器物、制度、称谓出现在本朝即为错。\n\n"
         if real_mode and era_hint else "")
        + f"你是网文主编，正在逐章审读《{title}》第 {n} 章（{pass_name or '通读'}）。"
        f"请**真读正文**，不要只看指标。\n\n" + "\n\n".join(parts) + "\n\n"
        f"===\n按以下维度打分（0-100）并给出问题：\n{dims}\n\n"
        f"重点抓这几类（这些是统计查不出来的）：\n"
        f"- 叙述拐杖：反复使用同一种叙述装置（如每章都写「某人在脑中冷静计算」）\n"
        f"- 身份/官职凭空变化，前文从未交代\n"
        f"- 视角越界：让某个角色知道他不可能知道的事\n"
        f"- 与「已确立的不可逆事实」冲突（死了的人又活了、毁掉的东西又出现）\n"
        f"- 钩子或句式与前文雷同\n\n"
        f"另外必须抽出 new_facts：**本章确立的、以后不能推翻的事实**。\n"
        f"  算：某人死亡、某人升迁/贬黜、某人叛变、某物被毁、身份被揭穿、重大承诺、"
        f"**签约/入伙/合作达成/关系确立**、重要据点或资产的取得与位置。\n"
        f"  不算：临时的位置、情绪、正在进行的计划。\n"
        f"  没有就给空数组，宁缺毋滥。\n\n"
        f"只输出 JSON（不要代码围栏），格式：\n{CRITIQUE_SCHEMA}\n"
        f"issues 最多 6 条，只报**有正文原句为证**的。")


def parse(raw: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {}
    sc = d.get("scores") or {}
    vals = [v for v in sc.values() if isinstance(v, (int, float))]
    d["overall"] = round(sum(vals) / len(vals)) if vals else None
    return d


def merge_canon(canon: List[Dict[str, Any]], new_facts: List[Dict[str, Any]],
                chapter: int, limit: int = 300) -> tuple:
    """把本章确立的事实并进台账，返回 (新台账, 新增条数)。"""
    seen = {(c.get("subject"), c.get("kind")) for c in canon}
    added = 0
    for f in (new_facts or [])[:6]:
        if not isinstance(f, dict):
            continue
        subj, fact = str(f.get("subject", "")).strip(), str(f.get("fact", "")).strip()
        if not subj or not fact or len(fact) > 80:
            continue
        key = (subj, f.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        canon.append({"chapter": chapter, "subject": subj, "fact": fact[:80],
                      "kind": f.get("kind", "other")})
        added += 1
    return canon[-limit:], added

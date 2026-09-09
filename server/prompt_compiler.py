"""提示词编译器 —— 把项目设定编译成网文作者真正在用的那种提示词。

来源: 线上真实用户提交的生产提示词 (v5.2 日志)。相比"提示词工程腔"的长段落描述,
实战里有效的是这几个手法, 全部在这里编译出来:

  1. #标题 块状结构        模型对块状标记的遵守度远高于长段落
  2. 人物资料卡 + [本章未出现] 标记   作者手动控场, 而不是让模型自己猜谁该出场
  3. 正向词库 (尽量多使用)  只给黑名单模型会写得干巴; 给白名单才有网感
  4. 【字数标记xx字】每 N 字一块   让模型自己数着写, 这是字数达标最有效的土办法
  5. 编号剧情清单 剧情1..剧情N     细纲写成动作清单而非散文, 模型才不会跑偏
  6. 明确的负面指令        "不要过度延申""不要在结尾进行总结"
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def to_plot_list(outline: str) -> List[str]:
    """把章节细纲规整成编号剧情清单。

    细纲可能是散文、可能已经是列表、也可能是"核心事件：…"这种字段式,
    统一拍平成一条条可执行的动作。
    """
    if not outline:
        return []
    # 已经是 剧情N: / 1. / - 形式
    items = re.findall(r"^\s*(?:剧情\s*\d+\s*[:：]|[-*·]\s*|\d+[、.)]\s*)(.+)$",
                       outline, re.M)
    if len(items) >= 3:
        return [re.sub(r"\s+", " ", x).strip() for x in items if len(x.strip()) > 4]

    # 字段式: 抽出「核心事件 / 爽点 / 章末钩子」的内容
    fields = re.findall(r"^\s*(?:核心事件|主要情节|爽点|冲突|章末钩子|结尾)\s*[:：]\s*(.+)$",
                        outline, re.M)
    out: List[str] = []
    for f in fields:
        out += [s.strip() for s in re.split(r"[；;。]\s*", f) if len(s.strip()) >= 2]
    if out:
        return out

    # 兜底: 按句号切
    body = re.sub(r"^第\s*\d+\s*章.*$", "", outline, flags=re.M)
    return [s.strip() for s in re.split(r"[。；\n]\s*", body) if len(s.strip()) > 6][:12]


def cast_block(roster: List[Dict[str, str]], chapter_outline: str,
               protagonist: str = "", max_cards: int = 16) -> str:
    """人物资料卡 + [本章未出现] 标记。

    全员都列出来 (让模型知道这个世界有谁), 但明确标注本章谁出场 ——
    这样既不会凭空冒出新角色, 也不会把不该出场的人硬拉进来。
    """
    if not roster:
        return ""
    lines: List[str] = []
    for c in roster[:max_cards]:
        name = c.get("name", "")
        if not name:
            continue
        digest = c.get("digest") or _digest(c.get("card", ""))
        present = bool(name and name in chapter_outline) or name == protagonist
        mark = "" if present else "[本章未出现]"
        lines.append(f"{mark}{name}：{digest}")
    if protagonist:
        lines.insert(0, f"（主角是「{protagonist}」，全文一律这样称呼）")
    return "\n".join(lines)


def voice_block(roster, chapter_outline: str, protagonist: str = "") -> str:
    """本章出场角色的**声音卡** —— 只给出场的人，且给全。

    压成一行的人物摘要（_digest）足够交代「他是谁」，交代不了「他怎么说话」。
    分章独立生成时没有可引用的语感锚，所有人都会说同一种腔调的金句 ——
    实测某书读到一百多章，玳安、老书吏、何九叔的话换个名字看不出差别。
    自称与口头禅是锚，原声样本是可模仿的样，禁用词是负向约束。
    """
    out = []
    for c in roster or []:
        name, card = c.get("name", ""), c.get("card", "")
        if not name:
            continue
        if not (name in (chapter_outline or "") or name == protagonist):
            continue
        got = []
        for key, _hint in VOICE_FIELDS:
            m = re.search(rf"\*?\*?{key}\*?\*?\s*[:：]\s*([^\n]+)", card)
            if m and m.group(1).strip():
                got.append(f"{key}：{m.group(1).strip()[:90]}")
        if got:
            out.append(f"【{name}】" + "　".join(got))
    if not out:
        return ""
    return ("#本章人物的说话方式（照着原声样本的腔调写，别让谁都说同一种话）\n"
            + "\n".join(out))


def _digest(card: str, limit: int = 150) -> str:
    """把多行角色卡压成一行 —— 真实提示词里人物卡都是一行一个。"""
    parts = []
    for key in ("身份", "年龄", "性格三词", "核心动机", "与主角关系",
                "口头禅", "专属口头禅或说话习惯"):
        m = re.search(rf"\*?\*?{key}\*?\*?\s*[:：]\s*([^\n]+)", card)
        if m:
            parts.append(m.group(1).strip().rstrip("。"))
    s = "，".join(parts) if parts else re.sub(r"\s+", " ", card)
    return s[:limit]


def compile_chapter_prompt(*, title: str, index: int, target_words: int,
                           genre_line: str, manner: str, alias_rule: str = "",
                           style_pack: Optional[Dict[str, Any]] = None,
                           extra_directive: str = "",
                           global_rules: Optional[List[str]] = None,
                           directives: Optional[List[str]] = None,
                           character_rules: Optional[List[str]] = None,
                           background: str, world_digest: str,
                           roster: List[Dict[str, str]], protagonist: str,
                           relations: str, mainline: str,
                           chapter_outline: str,
                           positive: List[str], negative: List[str],
                           constraints: str = "", memory: str = "",
                           block_words: int = 500) -> str:
    """编译单章正文提示词。"""
    plots = to_plot_list(chapter_outline)
    blocks = max(1, round(target_words / block_words))
    # 剧情条数必须与字数配额匹配 —— 实测给 10 条剧情写 2600 字, 模型会写到 4300 字。
    # 每条剧情大约需要 block_words*0.8 字才能展开, 超出的合并进最后一条。
    max_plots = max(3, int(target_words / (block_words * 0.8)))
    if len(plots) > max_plots:
        head, tail = plots[:max_plots - 1], plots[max_plots - 1:]
        plots = head + ["；".join(tail)[:160]]
    plot_block = "\n".join(f"剧情{i+1}：{p}" for i, p in enumerate(plots)) or chapter_outline

    seg: List[str] = []
    seg.append(
        f"你是一个{genre_line}网文作者，我希望根据我给你 #写作背景 #人物资料卡 #本章剧情 "
        f"遵守规则创作{target_words}字左右的章节小说。"
        f"写作手法需要{manner}，不要过度延申，不要在结尾进行总结，"
        f"对于剧情中的内容在正文中需要用网文作者的口吻去描写，包括语言、行为、人物。"
        f"字数是硬指标：{target_words} 字左右，"
        f"**超过 {int(target_words*1.15)} 字视为不合格**，宁可写短也不要写长；"
        f"描写点到即止，不铺陈、不复述已知信息。"
        f"写完最后一条剧情立刻收尾留钩子，不要再展开新情节。")
    # 原来这里还要求「每写满 500 字换大段落并标【字数标记xx字】」——实测正文里
    # 该标记出现 0 次, 模型根本不照办, 而且这条和网文该有的短段落节奏正好相反。
    if directives:
        seg.append("\n【正文写法要求】\n" + "\n".join(f"- {d}" for d in directives))
    # 称谓规则必须放在最前面 —— 埋进「必守约束」里模型基本不看,
    # 实测第 11 章「西门庆」0 次而「林远」25 次。
    if alias_rule:
        seg.append("\n⚠️ " + alias_rule)
    # 全局去 AI 味纪律（所有书共享）在前, 文风包题材纪律在后
    if character_rules:
        seg.append("\n⚠️ 【人物纪律·全局】所有人都是有自己算盘的人，不是推动剧情的道具\n"
                   + "\n".join(f"- {r}" for r in character_rules))
    if global_rules:
        seg.append("\n⚠️ 【写作纪律·全局】\n" + "\n".join(f"- {r}" for r in global_rules))
    sp = style_pack or {}
    op = sp.get("opening") or {}
    if op:
        seg.append("\n⚠️ 【开篇铁律】" + op.get("rule", "")
                   + "。" + op.get("forbid", "")
                   + (f"\n  正例：{'；'.join(op.get('good', [])[:2])}" if op.get("good") else "")
                   + (f"\n  反例（禁止）：{'；'.join(op.get('bad', [])[:2])}" if op.get("bad") else ""))
    pc = sp.get("paragraphChars")
    if pc and len(pc) == 2:
        # 段落长度是文风包的属性, 不是全局常数: 番茄要 15-30 字的极短段,
        # 起点历史可以到 60 字。全局纪律里只能写个泛泛区间, 这里按包覆盖。
        seg.append(f"⚠️ 【段落节奏】每段 {pc[0]}-{pc[1]} 字，一段只写一个动作或"
                   f"一句话；超过 {pc[1]} 字必须断段。手机端阅读，长段劝退。")
    pb = sp.get("pleasureBeats") or {}
    if pb.get("beats"):
        # 只说「每章一个爽点」模型就写成「谈成了/赢了」—— 赢了但不爽。
        # 爽点是结构：压→显→翻→补刀，缺哪一拍读者的气都出不透。
        seg.append("\n🔥 【爽点结构·本章至少完整走一遍】\n"
                   + "\n".join(pb["beats"])
                   + ("\n  别犯这些毛病：" + "；".join(pb.get("antipatterns", []))
                      if pb.get("antipatterns") else ""))
    ag = sp.get("antagonist") or {}
    if ag.get("rules"):
        seg.append("\n【对手规格】" + "；".join(ag["rules"]))
    db = sp.get("descriptionBudget") or {}
    if db.get("note"):
        seg.append("⚠️ 【描写配给】" + db["note"])
    ta = (sp.get("transitionAvoid") or {}).get("words")
    if ta:
        seg.append("【少用这些过渡与抒情词】" + "、".join(ta))
    if extra_directive:
        seg.append("⚠️ 【本书追加指令（用户自定义，优先级最高）】" + extra_directive)

    if genre_line:
        seg.append(f"\n#小说类型：{genre_line}")
    if background:
        seg.append(f"\n#写作背景\n{background.strip()}")
    if world_digest:
        seg.append(f"\n#世界观速览\n{world_digest.strip()}")

    cb = cast_block(roster, chapter_outline, protagonist)
    if cb:
        seg.append(f"\n#人物资料卡（标注[本章未出现]的角色本章不得登场）\n{cb}")
    vb = voice_block(roster, chapter_outline, protagonist)
    if vb:
        seg.append("\n" + vb)
    if relations:
        seg.append(f"\n#感情与关系线索\n{relations.strip()}")
    if mainline:
        seg.append(f"\n#主线剧情\n{mainline.strip()}")
    if memory:
        seg.append(f"\n#前情与记忆\n{memory.strip()}")
    if constraints:
        seg.append(f"\n#必守约束\n{constraints.strip()}")
    if negative:
        seg.append("\n#反向提示词库（禁止出现）\n" + "、".join(negative))
    if positive:
        seg.append("\n#正向提示词库（尽量多使用，写出网感）\n" + " ".join(positive))

    seg.append(f"\n#本章剧情（共 {len(plots)} 条，每条约 {int(target_words/max(1,len(plots)))} 字）"
               f"\n{plot_block}\n【剧情结束】")
    seg.append(f"\n再次确认：全章 {target_words} 字左右，写完 {len(plots)} 条剧情即收尾。"
               f"直接输出正文，不要任何前言、标题或说明。")
    return "\n".join(seg)


#: 分章细纲的字段契约 —— **唯一的一份**。
#:
#: 提示词里写一份格式、落盘守卫里再写一份检查，两边一定会漂移：实测
#: replan_outline 的格式漏了「承接」，补完的五章全部没有承接字段；
#: 另有一批模型把钩子塞进「剧情6：钩子：」、爽点整批丢掉，而守卫只松松地
#: 查了「钩子」二字，照样放行。格式与检查必须同源。
#: (字段名, 是否必需, 提示词里的说明)
#: 人物声音卡的字段。角色档案原来只有一段散文描述，人物只有动作没有语感 ——
#: 分章独立生成时说话会趋同：谁都在说「这账，平是不平」。
#: 自称与口头禅是可引用的锚，三句原声样本是可模仿的样本，禁用词是负向约束。
VOICE_FIELDS = [
    ("自称", "他管自己叫什么（武二／老夫／老娘／小的／我）"),
    ("口头禅", "他反复说的一两句话或口癖，不超过 8 字"),
    ("语感", "一句话说清他说话的样子：长短句、粗细、绕不绕弯、爱不爱反问"),
    ("原声样本", "三句他会说的话，用｜分隔。要能一眼认出是他，不是别人"),
    ("禁用词", "他绝不会用的词，用、分隔（比如粗人不说文绉绉的词）"),
]


OUTLINE_FIELDS = [
    ("一句话", True,
     "（**先写这一栏**：把这一章压成一句话，40 字以内，写清「谁做了什么、结果如何」。"
     "这句话会被喂给后面每一批当前情 —— 后面几百章能不能接住这一章，全看它。"
     "先定了这一句，下面的剧情才有主心骨；不许只重复章名，"
     "不许写「他做了个决定」这种没有信息的话）"),
    ("承接", True,
     "（用一句话写清这一章从上一章的什么地方接上来 —— 上一章的钩子怎么落地、"
     "谁在等什么、时间过了多久）"),
    ("出场角色", True, "（从可用角色里挑，至少 3 人，主角之外要有 2 个配角有戏）"),
    ("剧情1", True, "（一个具体动作或事件，一句话）"),
    ("重场", True,
     "（这一章哪一条剧情是**重头戏**，写「剧情N」。重场那一拍要占本章一半篇幅，"
     "其余是过场 —— 不标的话六条剧情等重，正文会平均用力，全章一个调门）"),
    # 只说「读者爽在哪」, 模型会写成**编辑评语**而不是爽点 —— 实测收到
    # 「通过第三方视角间接交锋, 既避免了主角正面硬刚的危险, 又通过摔杯
    # 具象化了武松的杀气」这种点评写法, 甚至把「虚惊一场」填进爽点字段。
    # 评语不是爽点: 写正文的那一遍照着评语写不出任何东西来。
    # 所以要求写成**具体事件**, 并点名禁掉分析腔。
    ("爽点", True,
     "（写**发生了什么**, 不是点评这一章的写法。格式：谁 + 在什么场合 + "
     "被怎样了 / 主角拿到了什么。要能直接当一场戏来写。\n"
     "  正例：王婆当着满茶坊的人被自己收的银子噎住，只能笑着咽下\n"
     "  反例：展现了主角的冷静／具象化了对手的杀气／形成张力／虚惊一场\n"
     "  这一章确实没有爽点就写「无」，不许拿情绪描写凑数）"),
    ("章末钩子", True, "（具体的钩子：新威胁／反常细节／未接的消息，不许写万金油）"),
]

OUTLINE_REQUIRED = [f for f, req, _ in OUTLINE_FIELDS if req]


def outline_format_block(plots_per_chapter: int = 6, cap: int = 0) -> str:
    """按字段契约生成「每章按此格式输出」那一段。

    `cap` 是单章细纲的字数上限。不给上限的话细纲会一路发胖：实测某书
    从第 1-50 章的 617 字/章涨到第 201-250 章的 1266 字，翻了一倍多，
    而没有任何东西在看着它。细纲写到正文的一半长，写正文就变成了扩写，
    成品会像注水的细纲。
    """
    lines = ["第N章 章节名"]
    for name, _req, hint in OUTLINE_FIELDS:
        if name == "剧情1":
            lines.append(f"剧情1：{hint}")
            lines.append("剧情2：…")
            lines.append("剧情3：…")
            per = int(cap / max(1, plots_per_chapter) * 0.8) if cap else 0
            lines.append(f"（每章 {plots_per_chapter} 条剧情，要能直接照着写，"
                         f"不要写成概括）")
            if cap:
                lines.append(f"⚠ **一条剧情一句话，{per} 字以内**；"
                             f"整章细纲控制在 {cap} 字以内。"
                             f"细纲是给写手的路条，不是正文的缩写 —— "
                             f"写满了，写正文就只剩扩写，成品会平。")
        else:
            lines.append(f"{name}：{hint}")
    return "\n".join(lines)


def compile_outline_prompt(*, title: str, start: int, count: int,
                           genre_line: str, world_digest: str,
                           roster_names: List[str], outline: str,
                           standby_names: Optional[List[str]] = None,
                           prev_summary: str, constraints: str,
                           plots_per_chapter: int = 6,
                           outline_cap: int = 0,
                           character_rules: Optional[List[str]] = None,
                           used_titles: Optional[List[str]] = None) -> str:
    """编译分章细纲提示词 —— 输出编号剧情清单，而不是散文。"""
    # 先算好可选段落再拼；直接在 f-string 序列里插 `+ (...)` 会打断隐式拼接
    used_block = (f"#已用过的章节名（本批一律不得重复，也不得只改一两个字）\n"
                  f"{'、'.join(used_titles[-60:])}\n\n") if used_titles else ""
    # 白名单三级：主力有档案，备选是总纲/骨架点过名的人（用了自动补档），
    # 再不够才走申报手续。原来只有「只能从中挑，不得凭空造人」这一句禁令，
    # 于是总纲承诺过的人只要开书那次没进花名册，全书就再也不会出场。
    standby_block = (f"**备选**（总纲或阶段骨架点过名，还没建档；本批要用就直接用，"
                     f"用了会自动补档）：{'、'.join(standby_names)}\n"
                     ) if standby_names else ""
    rules_block = (f"#人物纪律（对每一章都成立）\n"
                   + "\n".join(f"- {r}" for r in character_rules) + "\n\n"
                   ) if character_rules else ""
    return (
        f"你是{genre_line}的网文策划。为《{title}》写第 {start}-{start+count-1} 章的细纲。\n\n"
        f"#总纲\n{outline}\n\n"
        f"#世界观速览\n{world_digest}\n\n"
        f"#可用角色\n"
        f"**主力**（有完整档案，随时可用）：{'、'.join(roster_names)}\n"
        f"{standby_block}"
        f"以上都不够用时，可以**申报**新人，但必须走手续（见下方输出格式），"
        f"不许在剧情里凭空冒出一个没申报过的名字。\n\n"
        f"#前情\n{prev_summary}\n\n"
        f"{used_block}"
        f"#必守约束\n{constraints}\n\n"
        f"{rules_block}"
        f"每章严格按下面格式输出，章与章之间用一行 ###fenge 分隔：\n\n"
        f"{outline_format_block(plots_per_chapter, outline_cap)}\n"
        f"（本批第一章的「承接」要接住【前情】里给出的上一章结尾）\n"
        f"⚠ {len(OUTLINE_REQUIRED)} 个字段一个都不能少，"
        f"尤其是**重场**、**爽点**与**章末钩子**："
        f"不许把钩子塞进剧情条目里，缺字段的章会被整章丢弃重排。\n\n"
        f"衔接要求（最容易塌的地方，逐条对照）：\n"
        f"- 每一章的「承接」必须真的对上上一章的「章末钩子」，"
        f"不许把钩子晾着不管、下一章另起一摊事\n"
        f"- 时间要连得上：隔了多久就写多久，不许上一章深夜、下一章突然开春\n"
        f"- 人在哪要连得上：上一章人在东京，下一章不能凭空回到阳谷\n"
        f"- **本批第一章的接缝最容易断**，它前面那章不是你写的，"
        f"必须照着【前情】把它接住\n"
        f"- 钩子不许连着同一种（都是「有人来报」「有人拦路」），"
        f"也不许写完下一章就当没发生过\n\n"
        f"确有必要引入新角色时，在该章末尾单起一行申报（没有就不写）：\n"
        f"新角色：姓名|身份|因何而来|挂靠于（已有的某个角色或某个组织）\n"
        f"—— 挂靠是硬要求：新人必须依附已有的人或势力，不能是孤魂野鬼。\n\n"
        f"直接输出，无前言。")

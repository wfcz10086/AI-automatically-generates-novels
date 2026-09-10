#!/usr/bin/env python3
"""老辣调提示词跑分台 —— 把「种子→卷要→回合推演→切章→细纲→正文」整条链路跑通并比对模型。

用法:
    python3 scripts/prompt_bench.py --models glm-5.3-flash,qwen3.8-flash --rounds 3
    python3 scripts/prompt_bench.py --idea "靖难之役，建文帝" --rounds 1

产物: reports/prompt_bench/<时间戳>/<模型>/r<轮次>/<阶段>.md  +  metrics.json
链路每一步单独调一次模型，前一步的输出是后一步的输入 —— 合起来问会退化成「规划」。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STYLE = json.loads((ROOT / "packs/style/laolatiao.json").read_text(encoding="utf-8"))
SEEDS = STYLE["片段种子库"]
TONE = STYLE["主调"]


def _env():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_env()
BASE = os.environ["NOVEL_GW4_URL"].rstrip("/")
KEY = os.environ["NOVEL_GW4_KEY"]


def call(model: str, prompt: str, max_tokens: int = 6000, temp: float = 0.9,
         retries: int = 3) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temp,
    }
    # GLM 系关不掉思考，只能调档；低档能把额度留给可见输出
    if model.startswith("glm"):
        body["reasoning_effort"] = "low"
    last = ""
    for i in range(retries):
        try:
            r = requests.post(f"{BASE}/chat/completions", json=body, timeout=300,
                              headers={"Authorization": f"Bearer {KEY}"})
            r.raise_for_status()
            d = r.json()
            msg = d["choices"][0]["message"]
            out = (msg.get("content") or "").strip()
            if out:
                return out
            last = "空输出（全进思考）"
        except Exception as e:                       # 网关抖动不该让整条链路断掉
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 + i * 3)
    return f"[[调用失败: {last}]]"


def pick(key: str, n: int = 2) -> str:
    return "\n\n".join(SEEDS[key][:n])


# ─────────────────────────── 链路的九步 ───────────────────────────

def p_seed(idea: str) -> str:
    return f"""你要为一本长篇通俗历史小说定种子。种子不是人设，是三个约束撞在一起产生的火花。

用户的想法：{idea}

输出下面八项，每项都要具体到能查证、能落笔：

【1】clock 时钟：一件与主角无关、有明确日期、读者已知结局的倒计时。写明 事件+起始日期+不干预多久落地+落地后主角的下场。
【2】shell 壳：主角占的身份，在真实历史里下场很惨且读者知道有多惨。不许是无名小卒，也不许是本来就赢的人。
【3】counter_move 反直觉动作（最重要）：把这个身份的常规反应**反过来**的那一个具体动作，动词开头一句话。
     检验标准：讲给熟悉这段历史的人听，他的第一反应必须是「他疯了？」。过不了就重想。
     再给出这个动作的**第一句台词**，全书书名应该就是它。
【4】cheat 金手指：必须是一套**分析世界的方法**（写明是哪个现代行当的看家本事），并写明它的**盲区**——它解决不了哪一类问题。
     禁止：一把枪、一个系统、一本秘籍、任何一次性道具。
【5】prepaid_emotion 预装情绪：6~10 个读者不用铺垫就有感情的真实人名/事件，标明读者对每个预存的是什么情绪。
【6】accounts 六本硬账：六个可数的状态账，名字要贴这本书。只进不退，每动一格必须付一笔可查的损失。
【7】volume_chain 但是链，至少 6 环：格式 {{solves: 解决什么, exposes: 解决后新长出什么问题}}。
     硬约束 volume[i].exposes == volume[i+1].solves。每一环的解法必须让上一环的解法失效，不许写成能力叠加。
【8】planted_misreads：主角准备主动生产的误读 2~3 条——他自己造、别人愿意相信、可以反复提款的假解释。

不许输出「三条感情线」「主角性格缺陷」「爽点密度表」。种子只管发散，不管排布。"""


def p_faction(seed: str) -> str:
    return f"""种子：
{seed}

根据上面的种子，列出 5~7 个**非主角势力**（含敌对、中立、名义友军）。每个输出：
名称 / 它最想要什么 / 它内部谁和谁在争，争什么 / 它现在的实力状态（可数）/ 它对主角的**当前认知**（多半是错的）。

硬要求：至少两个势力之间有直接利害冲突，且与主角无关。它们大部分时间在互相咬，不是都在针对主角。
只输出清单，不要展开剧情。"""


def p_turn(seed: str, factions: str) -> str:
    return f"""这是一次【世界回合】。主角不在场，不许提到主角在做什么。

时钟：{_field(seed,'clock')}
各势力当前状态：
{factions}

让上面每一个势力，按【它自己的目标和它自己的内部矛盾】各自往前走一步。每个势力输出：
  它这段时间最想要什么 / 它内部谁和谁在争 / 它实际做了什么（一个具体动作，不许写「继续发展」）
  / 它自己的账变成了多少 / 它自认为聪明但没看见的隐患

硬要求：
- 至少一个势力做出【对它自己不利】的动作，因为内部斗争压倒了外部理性
- 至少一个势力因为误判主角而做出动作
- 不许所有势力都在针对主角"""


def p_conflict(turn: str, accounts: str) -> str:
    return f"""世界自转的结果：
{turn}

主角当前六本账：
{accounts}

上面哪一条，构成【主角不回应就会掉一格账】的局面？
写清楚：是哪一格、掉多少、多久之内、掉了之后他会怎么样。
如果有多条，选最急的那一条，并说明为什么它比其他几条更急。
只回答这一问，不要提出对策。"""


def p_obvious(situation: str) -> str:
    return f"""局面：
{situation}

一个正常人在这个位置上会怎么做？列出恰好 3 个所有人都预期他会做的选项。
每个选项写明：谁会支持、代价是什么、为什么它是「正常」的。

这一问的目的是把平庸方案穷举出来。不要给出第四个有创意的选项。"""


def p_counter(situation: str, obvious: str, counter_move: str, cheat: str) -> str:
    return f"""局面：
{situation}

三个常规选项（主角**不能**用这三条里的任何一条）：
{obvious}

主角的性子：{counter_move}
主角的金手指（一套看世界的方法）与它的盲区：{cheat}

这个主角会怎么做？必须同时满足：
（a）不在上面三个选项里
（b）用的是他的【方法】，不是力量
（c）在他自己的逻辑里完全说得通，一句话能讲清
（d）第一次听说的人反应是「他疯了？」
如果你想出来的方案过不了（d），推倒重来，不要交平庸答案。

再给三样东西：
- 这个动作的**真实意图**（往往很小、很私人：他只是困了 / 他只是不想戴绿帽子）
- 它要付的**代价**（谁受损、损多少、能不能恢复）
- 一句**台词**：口语、反常识、最好带点不要脸或自嘲
  参考：「孤家带兵来尽孝」「父皇别跑，儿臣孝顺」「大金不做选择题」「你过河、我拆桥」"""


def p_misread(action: str, factions: str) -> str:
    return f"""主角刚做的动作与他的真实意图：
{action}

下面每一个人都**不知道**他的真实意图。你在写他们的时候也不许用上帝视角。
观察者（连同他们各自的处境）：
{factions}

对每个观察者输出五项：
  who       他是谁
  because   他凭什么这么想——他的处境、他的知识、他吃过的亏、他此刻正在怕的事
  concludes 他得出的【错误】结论。必须比真相更大、更凶险
  acts      他因此做了什么（一个具体动作）
  spawns    这个动作制造了什么新局面

硬要求：
- 每个人的误读方向必须不同。三个人读出三个不一样的阴谋，不是同一个阴谋的三种说法
- 至少一个误读对主角有利，至少一个不利
- 至少一条要到 30 章以后才结账（标注 alive_until）
- **不许有人猜对**。猜对了就没有故事了

参考（真实案例）：
  动作：赵楷在陈桥驿扎营过夜。真实意图：他太困了，想睡一觉。
  ① 崇政殿宰执｜凭「陈桥驿是太祖黄袍加身处」｜结论：他要称帝｜
     行动：集体闭嘴不敢弹劾｜生出：朝廷失去了对他的全部约束力
  ② 自己的部下｜凭同一条典故｜结论：该劝进了｜
     行动：商量去找一件黄袍｜生出：主角被吼醒，被迫当场表态并顺势整编军队

最后单独一行：spawns 里最大的那一条是哪个——它将成为下一回合的输入。"""


def p_cut(action: str, misread: str) -> str:
    tt = "\n".join(
        f"  - {t['式']}｜例：{'、'.join(t['例'][:3])}"
        for t in STYLE["章标题模板库"]["模板"])
    return f"""事件（主角的动作 + 真实意图 + 代价）：
{action}

围绕它的误读：
{misread}

第一问：这个事件值几章？
  参考配比——决定国运的大战役 15~25 章；一场攻防或一次朝堂摊牌 5~8 章；
  一次谈判/抄家/科举 3~5 章；日常推进 1~2 章。
  值几章就写几章，不许为了凑数把一个事件掰成几个假事件。

第二问：这几章分别用**谁的眼睛**看？
  硬规则：同一事件内不得重复视角；**过半必须是对手或输家的眼睛**；
  主角本人视角不得超过三分之一；至少一章用小人物的眼睛
  （一个亲兵、一个画师、一个酒楼掌柜、一个验尸的仵作）。

第三问：这几章的标题。必须是台词、反问或喊话，禁止意象式偏正结构。
  同一事件可以连用完全相同的标题，也可以加（一）（二）。从下面模板取：
{tt}

第四问：这个事件在**哪一章**动账？只能动一次。写明 from → to。

输出成一张表：章序 | 视角 | 标题 | 本章干什么 | 是否动账"""


def p_outline(cut: str, action: str, misread: str) -> str:
    return f"""下面是一个事件的切章表：
{cut}

事件详情：{action}
活跃误读：{misread}

请为**表中第 1 章**写章细纲。输出以下字段，缺一不可：

标题　　一句台词/反问/喊话
视角　　本章从谁的眼睛看；他现在的处境、他今天最怕什么、他今天想要什么
承接　　上一章章末那条新消息在本章第几段落地
动作　　主角做的那一个动作（动词开头，一句话）
误读　　本章新产生或被消费的误读：谁 + 凭什么 + 读成什么 + 因此干了什么
后果　　这个误读生出的新麻烦（下一章的输入）
账目　　本章动了哪一格：{{账名}} from {{旧值}} → to {{新值}}；不动账就写「本章不动账」
代价　　谁受损、损多少、可否恢复；没有就写「本章无代价」
解说　　本章要讲透的那一条制度/数字/器物（一句话）
重场　　哪一段是本章的重心
章末　　一条新消息/一个新名字/一句让读者替某人着急的话
　　　　禁止：感想、总结、决心、「他知道，这只是开始」

反面例子（别这样写）：「爽点：本地权力结构在枪威与金钱下迅速向主角倾斜。」
——这是对写法的点评，不是事件。要写「谁在什么场合被怎样了 / 主角拿到了什么」。"""


def p_draft(outline: str) -> str:
    return f"""本章细纲：
{outline}

── 调子 ──
{TONE}

下面是同类作品里几段真东西。不要抄内容，听它的嗓子：

【外部视角开场】
{pick('外部视角开场')}

【反讽旁白·把正经话戳破】
{pick('反讽旁白·把正经话戳破', 4)}

【误读推断·别人算错】
{pick('误读推断·别人算错')}

【算自己的账】
{pick('算自己的账')}

【制度解说·把规矩讲透】
{pick('制度解说·把规矩讲透', 1)}

【断章钩子】
{pick('断章钩子', 1)}

── 硬指标（写完会被机器量，不达标退回重写）──
正文 2400~3400 字
对白占正文比 20%~28%
每千字问号 4.5~6.5；每千字叹号 7.5~10.5
段落平均字数 40~62，单段不得超过 200 字
独立成段的反问句 3~6 个（必须是角色的疑问，不是叙述者的）
反讽旁白 2~4 处
至少 1 处别人对主角的错误推断，并写明他凭什么这么想
至少 1 段把一条制度/数字/器物讲透，讲完立刻接一句大白话戳破

── 硬规矩 ──
1. 开场用细纲指定的视角人的眼睛，不是主角的。前 300 字里主角可以不出现。
2. 配角开口前，先让读者知道这句话对他自己有什么好处。
3. 不许替主角辩护。叙述者可以笑他，不许夸他。
4. 章末不许是感想、总结、决心。
5. 本章的账只能按细纲写的动那一格，其余五格原样不动。

直接输出正文，不要任何说明、标题外的前言或结语。"""


def _field(seed: str, name: str) -> str:
    m = re.search(rf"{name}[^\n]*\n(.{{0,600}})", seed, re.S)
    return m.group(1).strip() if m else seed[:600]


# ─────────────────────────── 验收闸 ───────────────────────────

IRONY = STYLE["词表"]["反讽旁白引导词"]
MISREAD_W = STYLE["词表"]["误读推断词"]
EXPLAIN_W = ["所谓", "其实", "这就是", "说穿了", "通常情况下", "实际上", "规矩", "制度"]


def gate(text: str) -> dict:
    body = re.sub(r"^#.*$", "", text, flags=re.M).strip()
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    n = len(body) or 1
    dlg = sum(len(x) for x in re.findall(r"[「『\"“][^」』\"”]{0,300}[」』\"”]", body))
    m = {
        "字数": len(body),
        "对白占比": round(dlg / n, 3),
        "每千字问号": round(body.count("？") / n * 1000, 2),
        "每千字叹号": round(body.count("！") / n * 1000, 2),
        "段落数": len(lines),
        "段均字数": round(sum(len(l) for l in lines) / max(1, len(lines)), 1),
        "最长段": max((len(l) for l in lines), default=0),
        "独立反问句": sum(1 for l in lines if l.endswith("？") and len(l) < 45),
        "反讽旁白": sum(body.count(w) for w in IRONY),
        "误读推断": sum(body.count(w) for w in MISREAD_W),
        "解说体": sum(body.count(w) for w in EXPLAIN_W),
        "结尾完整": bool(body) and body[-1] in "。！？…」』\"”",
    }
    fails = []
    if not 2400 * 0.85 <= m["字数"] <= 3400 * 1.25: fails.append("字数")
    if not 0.20 <= m["对白占比"] <= 0.28: fails.append("对白占比")
    if not 4.5 <= m["每千字问号"] <= 6.5: fails.append("问号密度")
    if not 7.5 <= m["每千字叹号"] <= 10.5: fails.append("叹号密度")
    if not 40 <= m["段均字数"] <= 62: fails.append("段均字数")
    if m["最长段"] > 200: fails.append("单段过长")
    if not 3 <= m["独立反问句"] <= 6: fails.append("独立反问句")
    if not 2 <= m["反讽旁白"] <= 5: fails.append("反讽旁白")
    if m["误读推断"] < 1: fails.append("无误读")
    if m["解说体"] < 1: fails.append("无解说")
    if not m["结尾完整"]: fails.append("结尾截断")
    m["未过项"] = fails
    m["通过"] = not fails
    return m


# ─────────────────────────── 跑一轮 ───────────────────────────

STAGES = ["1_seed", "2_factions", "3_turn", "4_conflict", "5_obvious",
          "6_counter", "7_misread", "8_cut", "9_outline", "10_draft"]


def run_round(model: str, idea: str, out: Path, rnd: int) -> dict:
    d = out / model / f"r{rnd}"
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log = {}

    def step(name, prompt, mt=6000, temp=0.9):
        s = time.time()
        r = call(model, prompt, mt, temp)
        (d / f"{name}.md").write_text(
            f"<!-- prompt {len(prompt)} 字 / 输出 {len(r)} 字 / {time.time()-s:.0f}s -->\n\n{r}",
            encoding="utf-8")
        log[name] = {"chars": len(r), "sec": round(time.time() - s, 1),
                     "failed": r.startswith("[[调用失败")}
        print(f"  [{model} r{rnd}] {name}: {len(r)} 字 / {time.time()-s:.0f}s", flush=True)
        return r

    seed = step("1_seed", p_seed(idea), 7000, 0.95)
    fac = step("2_factions", p_faction(seed), 5000)
    turn = step("3_turn", p_turn(seed, fac), 6000)
    conf = step("4_conflict", p_conflict(turn, _field(seed, "accounts")), 3000, 0.7)
    obv = step("5_obvious", p_obvious(conf), 3000, 0.7)
    cm = step("6_counter", p_counter(conf, obv, _field(seed, "counter_move"),
                                     _field(seed, "cheat")), 4000, 1.0)
    mis = step("7_misread", p_misread(cm, fac), 5000, 0.95)
    cut = step("8_cut", p_cut(cm, mis), 4000)
    out_l = step("9_outline", p_outline(cut, cm, mis), 3500)
    draft = step("10_draft", p_draft(out_l), 8000, 0.92)

    g = gate(draft)
    (d / "metrics.json").write_text(
        json.dumps({"model": model, "round": rnd, "idea": idea,
                    "总耗时秒": round(time.time() - t0, 1),
                    "各步": log, "正文验收": g}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"  [{model} r{rnd}] 完成 {time.time()-t0:.0f}s | 验收 "
          f"{'通过' if g['通过'] else '未过: ' + '、'.join(g['未过项'])}", flush=True)
    return {"model": model, "round": rnd, "gate": g, "log": log,
            "sec": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="glm-5.3-flash,qwen3.8-flash")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--idea", default="靖难之役，建文帝朱允炆")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    out = Path(a.out) if a.out else ROOT / "reports/prompt_bench" / time.strftime("%m%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    print(f"跑分台 → {out}\n模型 {models} × {a.rounds} 轮，题目：{a.idea}\n", flush=True)

    jobs = [(m, r) for m in models for r in range(1, a.rounds + 1)]
    res = []
    with cf.ThreadPoolExecutor(max_workers=min(4, len(jobs))) as ex:
        futs = {ex.submit(run_round, m, a.idea, out, r): (m, r) for m, r in jobs}
        for f in cf.as_completed(futs):
            try:
                res.append(f.result())
            except Exception as e:
                m, r = futs[f]
                print(f"  [{m} r{r}] 崩了: {e}", flush=True)

    (out / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print("\n════ 汇总 ════")
    for m in models:
        rs = [x for x in res if x["model"] == m]
        if not rs:
            continue
        ok = sum(1 for x in rs if x["gate"]["通过"])
        fails = {}
        for x in rs:
            for k in x["gate"]["未过项"]:
                fails[k] = fails.get(k, 0) + 1
        avg = lambda k: round(sum(x["gate"][k] for x in rs) / len(rs), 2)
        print(f"\n{m}  {len(rs)} 轮，正文验收通过 {ok}/{len(rs)}，平均 {round(sum(x['sec'] for x in rs)/len(rs))}s")
        print(f"  字数 {avg('字数')} | 对白 {avg('对白占比')} | 问号 {avg('每千字问号')} "
              f"| 叹号 {avg('每千字叹号')} | 段均 {avg('段均字数')} | 最长段 {avg('最长段')}")
        print(f"  独立反问 {avg('独立反问句')} | 反讽 {avg('反讽旁白')} | 误读 {avg('误读推断')} | 解说 {avg('解说体')}")
        if fails:
            print("  未过项统计：" + "、".join(f"{k}×{v}" for k, v in
                                        sorted(fails.items(), key=lambda x: -x[1])))
    print(f"\n产物在 {out}")


if __name__ == "__main__":
    main()

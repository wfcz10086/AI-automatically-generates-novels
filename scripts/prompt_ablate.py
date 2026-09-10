#!/usr/bin/env python3
"""消融测试：验证「结算式提示词」和「真片段注入」到底有没有用。

三组，同模型、同局面、同细纲输入，只改提示词的做法：
  A 结算式 + 真片段   —— 完整方案
  B 规划式 + 真片段   —— 把五步推演换成一句「请规划接下来 5 章」
  C 结算式 + 无片段   —— 只用形容词描述文风，不贴原作片段
  D 现框架对照        —— 模仿 orchestrator 现在的做法（禁令堆叠、七字段细纲）

用法: python3 scripts/prompt_ablate.py --model glm-5.3-flash --rounds 2
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib.util
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("pb", ROOT / "scripts/prompt_bench.py")
pb = importlib.util.module_from_spec(_s)
_s.loader.exec_module(pb)

IDEA = "靖难之役，建文帝朱允炆"

# 四组共用的局面，写死，保证只有提示词做法这一个变量
SITUATION = """建文元年七月，燕王朱棣以「清君侧」起兵，一月内连下通州、蓟州、遵化、永平。
朝廷手上：京营兵三十万但久不习战；名将凋零，能用的只剩耿炳文（老迈）与李景隆（纨绔）。
主角朱允炆手上六本账：兵（京营三十万，堪战者不足五万）／地（南方全境，北平以北已失）／
名分（正统天子，但「清君侧」的口号让削藩成了他的原罪）／钱（江南税赋充足）／
技术（火器有，但握在工部老吏手里）／制度（削藩国策，无人敢言其非）。
方孝孺、齐泰、黄子澄三人正在争「谁该为削藩激变负责」。"""

COUNTER = "主角的性子：不装仁君，反而主动把「削藩」这口锅自己背到底，并且当众加码"
CHEAT = "金手指：现代法律人的方法（把一切争议拆成程序、举证责任与管辖权之争）。盲区：程序赢不了刀。"


def p_A_settle(model_seed: str) -> str:
    """A/C 共用的前半：五步结算，压缩成一次调用里的分步指令（消融只测最后出文）。"""
    return f"""局面：
{SITUATION}

第一问：上面哪一条构成【主角不回应就会掉一格账】的局面？是哪一格、掉多少、多久之内。
第二问：正常人在这个位置上会怎么做？列出恰好 3 个所有人都预期的选项，并写明各自代价。
第三问：{COUNTER}
        {CHEAT}
        这个主角会怎么做？必须（a）不在上面三条里（b）用方法不用力量
        （c）他自己逻辑里说得通（d）第一次听说的人反应是「他疯了？」。过不了（d）就推倒重来。
        再给：这个动作的真实意图（往往很小很私人）／它的代价（谁受损、损多少）／一句口语反常识的台词。
第四问：下面这些人都**不知道**他的真实意图，各自会怎么误读？
        （方孝孺／李景隆／朱棣／北平城里的一个百户／江南一个收税的书吏）
        每人写：他凭什么这么想 → 他得出的【错误】结论（必须比真相更大更凶险）→
        他因此做了什么 → 生出什么新局面。不许有人猜对，每人误读方向必须不同。
第五问：给这个事件切章，写出第 1 章的细纲。第 1 章必须用**非主角**的眼睛，
        标题必须是台词/反问/喊话。细纲含：标题/视角/动作/误读/后果/账目 from→to/代价/解说/章末。

按一二三四五分节输出。"""


def p_B_plan() -> str:
    return f"""局面：
{SITUATION}

请规划接下来 5 章的剧情，要求情节精彩、冲突强烈、有爽点、章末有钩子。
然后写出第 1 章的细纲，含：一句话 / 承接 / 出场角色 / 剧情1-6 / 重场 / 爽点 / 章末钩子。"""


def p_draft_with_seeds(outline: str) -> str:
    return pb.p_draft(outline)


def p_draft_no_seeds(outline: str) -> str:
    return f"""本章细纲：
{outline}

── 文风要求 ──
用现代内行人的眼睛看塌方的旧体制；人人算自己的账；叙述者比场上的人多知道一层，
写完正经话要用大白话戳破。语言老辣、口语化、有黑色幽默，段落短，对话多，情绪外放。
开场用非主角视角。章末不许是感想、总结、决心。

正文 2400~3400 字。直接输出正文，不要任何说明。"""


def p_draft_current_framework(outline: str) -> str:
    """模仿现有 orchestrator 的做法：禁令堆叠在前，风格用形容词描述。"""
    return f"""【已确立的不可逆事实，绝对不得推翻】建文元年七月燕王起兵；耿炳文老迈；李景隆纨绔
【时代红线·写进正文即穿帮】不得出现明代之后的器物、官制、称谓
【数字锁·已定死的数字，不得改口】京营三十万；堪战不足五万
【实力台账·只进不退，不得凭空跃升或倒退】朱允炆：正统天子，握江南税赋
【命名注册表】已有角色：朱允炆、朱棣、方孝孺、齐泰、黄子澄、耿炳文、李景隆。
　新配角绝不能与上述姓名相同或高度相似
【开篇必须换花样】本章开篇的时间词、地点、句式、视角不得与前几章雷同
【配角配额】本章除主角外至少让 2 个配角有独立台词与动作
【已被用滥的表达，本章最多出现 1 次】不禁、仿佛、似乎、五味杂陈
【禁止】总而言之、综上所述

平台文风：番茄爽文（番茄小说）
- 节奏第一：每章至少 1 个爽点或推进点
- 段落要短：单段不超过 3 行，大量使用单句成段制造节奏感
- 多对话少描写：对话占比 40% 以上
- 情绪外放：人物反应要写出来
- 章末必留钩子
- 爽点按四拍写：压→显→翻→补刀
【全局写作偏好】叙事视角：第三人称限制视角；时态：过去时

本章细纲：
{outline}

请写出本章正文，2400~3400 字。"""


def run(model: str, rnd: int, out: Path) -> list:
    d = out / f"r{rnd}"
    d.mkdir(parents=True, exist_ok=True)
    res = []

    def w(name, txt):
        (d / f"{name}.md").write_text(txt, encoding="utf-8")

    # 前半：A/C 共用结算结果；B 用规划结果；D 用 B 的细纲（模拟现框架的七字段细纲）
    settle = pb.call(model, p_A_settle(""), 7000, 0.95)
    w("_settle", settle)
    plan = pb.call(model, p_B_plan(), 5000, 0.95)
    w("_plan", plan)

    def tail(t):  # 取细纲部分
        m = re.search(r"(第五问|细纲)(.{200,})", t, re.S)
        return (m.group(0) if m else t)[-3000:]

    jobs = {
        "A_结算+片段": (p_draft_with_seeds, tail(settle)),
        "B_规划+片段": (p_draft_with_seeds, tail(plan)),
        "C_结算+无片段": (p_draft_no_seeds, tail(settle)),
        "D_现框架对照": (p_draft_current_framework, tail(plan)),
    }
    for name, (fn, ol) in jobs.items():
        t = time.time()
        txt = pb.call(model, fn(ol), 8000, 0.92)
        w(name, txt)
        g = pb.gate(txt)
        m = pb.measure(txt)
        res.append({"组": name, "轮": rnd, "model": model, "秒": round(time.time() - t),
                    "指标": m, "单章闸": g["通过"], "未过": g["未过项"]})
        print(f"  [{model} r{rnd}] {name}: {m['字数']}字 对白{m['对白占比']} "
              f"问{m['每千字问号']} 叹{m['每千字叹号']} 段均{m['段均字数']} "
              f"反讽{m['反讽旁白']} 误读{m['误读推断']} 解说{m['解说体']} "
              f"独立反问{m['独立反问句']}", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    out = ROOT / "reports/prompt_ablate" / f"{a.model}-{time.strftime('%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"消融 → {out}\n", flush=True)

    res = []
    with cf.ThreadPoolExecutor(max_workers=min(3, a.rounds)) as ex:
        for f in cf.as_completed([ex.submit(run, a.model, r, out)
                                  for r in range(1, a.rounds + 1)]):
            res += f.result()

    (out / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print("\n════ 消融汇总（目标区间见 packs/style/laolatiao.json）════")
    print(f"{'组':16s} {'字数':>6s} {'对白':>6s} {'问/千':>6s} {'叹/千':>6s} "
          f"{'段均':>6s} {'反讽':>5s} {'误读':>5s} {'解说':>5s} {'独反问':>6s}")
    for name in ["A_结算+片段", "B_规划+片段", "C_结算+无片段", "D_现框架对照"]:
        rs = [x["指标"] for x in res if x["组"] == name]
        if not rs:
            continue
        av = lambda k: round(sum(r[k] for r in rs) / len(rs), 2)
        print(f"{name:16s} {av('字数'):6.0f} {av('对白占比'):6.2f} {av('每千字问号'):6.2f} "
              f"{av('每千字叹号'):6.2f} {av('段均字数'):6.1f} {av('反讽旁白'):5.1f} "
              f"{av('误读推断'):5.1f} {av('解说体'):5.1f} {av('独立反问句'):6.1f}")
    print("\n原作 10 章窗口参考： 对白0.25 问5.33 叹8.94 段均54.7 "
          "反讽20/10章 误读6/10章 解说16/10章 独反问17/10章")
    print(f"产物在 {out}")


if __name__ == "__main__":
    main()

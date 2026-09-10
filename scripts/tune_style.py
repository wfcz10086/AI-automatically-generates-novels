#!/usr/bin/env python3
"""文风调教机 —— 把已写的章节量一遍，对着两本原作的真实画像报差距并给出改法。

调教不该靠眼力。这个脚本用**同一把尺**（server.prompt_compiler.measure_text）
量原作和自己的产出，逐项报「差多少、往哪边差、该动哪一条结构件」。

    python3 scripts/tune_style.py --project 老辣调端到端验证
    python3 scripts/tune_style.py --project X --target 抢救大明朝 --from 8

画像文件 packs/profiles/laolatiao.json 由 --rebuild-profile 重建（需要语料）。
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.prompt_compiler import measure_text            # noqa: E402

PROFILE = ROOT / "packs/profiles/laolatiao.json"
PACK = ROOT / "packs/style/laolatiao.json"

#: 额外词表指标 —— measure_text 只管句读层面，这些管「说的是哪一行的话」
EXTRA = {
    "着急词": ["怕", "慌", "急", "来不及", "完了", "坏了", "冷汗", "不敢", "要命", "糟了"],
    "好笑词": ["哈哈", "苦笑", "哭笑不得", "没好气", "白眼", "噎", "不要脸", "无耻",
             "厚颜", "忽悠", "坑", "好家伙"],
    "江湖话": ["没准", "门清", "眼热", "泼天", "架子货", "滑头", "撒银子", "递话", "打点",
             "背锅", "甩锅", "拿捏", "服软", "改口", "撕破脸", "上道", "路子", "心腹", "爪牙"],
    "行当黑话": ["基本面", "现金流", "资不抵债", "优质资产", "市场占有率", "资产重组", "打工",
              "催收", "韭菜", "风口", "本金", "回本", "亏本", "成本", "垄断", "加盟",
              "面试", "投资", "债主", "坏账", "假账"],
    "现代制度词": ["殖民", "封建", "共和", "议政", "资本主义", "立宪", "选举", "高考",
               "持久战", "根据地", "运动战", "金手指", "土改", "分田"],
}

#: 每个指标偏高/偏低时该动哪里 —— 调教的落点，不是笼统的「再写好一点」
FIX = {
    "每千字叹号": ("结构件「让有身份的人当众失态」：本章至少一次，演出来不许叙述",
              "失态写太多了，挑一个人狠狠丢一次脸就够，其余人保持体面"),
    "独立反问句": ("结构件「角色心里的问句」：人物不明白时单独起一段写出他心里那句问话",
              "独立问句太密，把一部分并回叙述"),
    "对白占比": ("结构件「对话」：至少三场成形对话、每场四个来回",
              "对白过量，把一部分换成制度解说与算账"),
    "解说体": ("细纲「解说」栏要写清讲到什么程度；正文里讲完立刻用大白话戳破",
             "解说过多，篇幅还给戏"),
    "句均字数": ("形制 sentenceChars 调高：允许用逗号把三四个小分句串成一长句",
             "形制 sentenceChars 调低：句子收短"),
    "段均字数": ("paragraphChars 调高；意思连着的两三句放同一段",
             "paragraphChars 调低；该砸节奏的那句单独成段"),
    "每千字问号": ("多让人物提问、追问", "少一点疑问句"),
    "反讽旁白": ("写完正经话用一句大白话戳破，靠句子转折不靠固定起手",
             "戳破太频，成念咒了；招牌起手整章最多一次"),
    "着急词": ("让人物处在「马上要出事」的状态里", "别人人自危，留出对比"),
    "好笑词": ("加不要脸/自嘲的台词，让人当众出丑", "笑点过密，收一收"),
    "江湖话": ("多用市井口语（没准/眼热/泼天的富贵/撒银子/递个话）", "江湖话太满"),
    "行当黑话": ("金手指是哪一行，就多用那一行的看家词汇", "行当黑话过量，会出戏"),
    "现代制度词": ("议题升级时该用现代制度词点破（殖民/封建/资本主义）", "现代词太多，穿帮风险"),
    "字数": ("正文目标字数调高；或让扩写补足", "写太长了，压缩"),
}


def profile_of(text_list):
    ms = []
    for t in text_list:
        m = measure_text(t)
        k = max(1, len(t)) / 1000
        for ek, ws in EXTRA.items():
            m[ek] = round(sum(t.count(w) for w in ws) / k, 3)
        sents = [len(x) for x in re.split(r"(?<=[。！？…])", t) if x.strip()]
        m["句均字数"] = round(statistics.mean(sents), 1) if sents else 0
        ms.append(m)
    return ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--target", default="both",
                    choices=["both", "大宋有种", "抢救大明朝"])
    ap.add_argument("--from", dest="frm", type=int, default=1, help="从第几章起算")
    ap.add_argument("--apply", action="store_true", help="把可自动调的项写回文风包")
    a = ap.parse_args()

    prof = json.loads(PROFILE.read_text(encoding="utf-8"))
    targets = list(prof) if a.target == "both" else [a.target]

    fs = sorted(glob.glob(str(ROOT / "projects" / a.project / "chapters" / "*.md")))
    fs = [f for f in fs if int(Path(f).stem) >= a.frm]
    if not fs:
        sys.exit(f"{a.project} 第 {a.frm} 章起没有正文")
    ms = profile_of([Path(f).read_text(encoding="utf-8") for f in fs])
    mine = {k: statistics.mean([m[k] for m in ms]) for k in ms[0]}

    keys = ["字数", "对白占比", "每千字问号", "每千字叹号", "句均字数", "段均字数",
            "独立反问句", "反讽旁白", "解说体"] + list(EXTRA)
    print(f"\n调教报告：{a.project}　第 {a.frm}~{Path(fs[-1]).stem} 章（{len(fs)} 章）")
    print(f"对标：{'、'.join(targets)}\n")
    head = f"{'指标':11s}{'我的':>9s}"
    for t in targets:
        head += f"{t[:4]+'(p10~p90)':>22s}"
    head += "  判定"
    print(head)

    todo = []
    for k in keys:
        line = f"{k:11s}{mine[k]:9.2f}"
        verdicts = []
        for t in targets:
            b = prof[t][k]
            line += f"{b['p10']:>9.2f}~{b['p90']:<12.2f}"
            if mine[k] < b["p10"]:
                verdicts.append(("低", (b["p10"] - mine[k]) / max(1e-9, b["p90"] - b["p10"])))
            elif mine[k] > b["p90"]:
                verdicts.append(("高", (mine[k] - b["p90"]) / max(1e-9, b["p90"] - b["p10"])))
        if not verdicts:                       # 落在所有目标的区间内
            print(line + "  ✓")
            continue
        d = verdicts[0][0]
        sev = max(v[1] for v in verdicts)
        # 只有对所有目标都偏同一边才算真偏；一高一低说明它落在两本之间，不算问题
        if len({v[0] for v in verdicts}) > 1:
            print(line + "  ~ 落在两本之间")
            continue
        tag = "严重" if sev > 1.0 else ("明显" if sev > 0.35 else "轻微")
        print(line + f"  ✗ 偏{d}（{tag}）")
        todo.append((sev, k, d, tag))

    if not todo:
        print("\n全部落在原作区间内。")
        return
    todo.sort(reverse=True)
    print("\n── 按严重度排的改法 ──")
    for sev, k, d, tag in todo:
        fix = FIX.get(k, ("（未登记改法）", "（未登记改法）"))
        print(f"[{tag}] {k}偏{d}　→　{fix[0] if d=='低' else fix[1]}")

    if a.apply:
        pack = json.loads(PACK.read_text(encoding="utf-8"))
        changed = []
        for sev, k, d, _ in todo:
            if k == "句均字数":
                lo, hi = pack.get("sentenceChars", [23, 31])
                step = 3 if d == "低" else -3
                pack["sentenceChars"] = [max(12, lo + step), max(16, hi + step)]
                changed.append(f"sentenceChars → {pack['sentenceChars']}")
            elif k == "段均字数":
                lo, hi = pack.get("paragraphChars", [47, 62])
                step = 5 if d == "低" else -5
                pack["paragraphChars"] = [max(20, lo + step), max(30, hi + step)]
                changed.append(f"paragraphChars → {pack['paragraphChars']}")
        if changed:
            PACK.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
            print("\n已写回文风包：" + "；".join(changed))
        else:
            print("\n（本轮没有可自动调的项，其余要改结构件措辞，需人工判断）")


if __name__ == "__main__":
    main()

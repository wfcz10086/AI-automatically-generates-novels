#!/usr/bin/env python3
"""测试窗口软反馈能不能突破 45% 的天花板。

闭环两轮的结论：八项结构约束同时压在每一章上，模型必然拆东墙补西墙，
平均相对差卡在 45%。假设：把单章常驻的结构件降到三条（对白/误读/解说），
其余靠「上个窗口漂移了什么」动态补，每章只需照顾两个漂移项，就不会互相抢配额。

三组，同样 6 章真章、同样的倒推细纲（直接复用上一轮存盘的），只改正文提示词：
  F_全量八条    —— 当前 p_draft，八项结构件全上（对照组，已知 45%）
  G_精简三条    —— 只留对白/误读/解说，其余不提
  H_精简+窗口反馈 —— 精简三条 + 一行「上个窗口这两项漂了，这章补一下」

用法: python3 scripts/window_feedback_test.py --outdir <上一轮闭环目录>
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, importlib.util, json, re, statistics, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("pb", ROOT / "scripts/prompt_bench.py")
pb = importlib.util.module_from_spec(_s); _s.loader.exec_module(pb)
SP = Path("/tmp/claude-1000/-opt-AI-automatically-generates-novels/"
          "c40d4bce-e75c-4ae0-91c9-15d8ced735b7/scratchpad")

# 原作 10 章窗口的目标区间（laolatiao.json 的数值硬指标）
TARGET = {"对白占比": (0.16, 0.34), "每千字问号": (3.4, 7.7), "每千字叹号": (6.6, 11.3),
          "段均字数": (47, 62), "独立反问句": (2, 5), "解说体": (1, 4)}
NAME_CN = {"对白占比": "对白偏{}", "每千字问号": "人物发问偏{}", "每千字叹号": "喊话偏{}",
           "段均字数": "句子/段落偏{}", "独立反问句": "独立成段的问句偏{}", "解说体": "把规矩讲透的段落偏{}"}
FIX = {"对白占比": {"低": "多写成形的对话，少用叙述概括", "高": "少一点对话，多一点叙述与解说"},
       "每千字问号": {"低": "多让人物提问、追问", "高": "少一点疑问句"},
       "每千字叹号": {"低": "多让人物把话喊出来，急了怕了气了就喊，用感叹号收尾", "高": "少喊，收着点"},
       "段均字数": {"低": "句子串长一点，意思连着的两三句放同一段", "高": "句子收短一点，段落切开一些"},
       "独立反问句": {"低": "人物不明白的时候，把他心里那句问话单独成段写出来", "高": "少用单独成段的问句"},
       "解说体": {"低": "多用一段把一条制度／数字／器物讲透", "高": "少讲一段规矩"}}

CORE = """── 本章必须包含的结构件 ──

▍对话
　至少三场成形的对话，每场至少四个来回。不许用「两人商议了一番」这种概括代替。
　每一场里至少有一个人是在替自己讨价还价，不是在传达信息。

▍别人算错
　本章至少两次，让某个人推断错。三步写法，缺一不可：
　　一、他看见了什么（具体的：一个动作、一句话、一件东西）
　　二、他凭什么这么想——他的处境、他吃过的亏、他此刻正在怕的事
　　三、他因此做了什么
　他必须猜错，而且错得比真相更大更凶险。

▍制度解说
　把细纲里那条规矩讲透，讲成读者能复述的程度。讲完立刻另起一段，用大白话戳破它。
"""


def p_lean(outline: str, feedback: str = "") -> str:
    fb = f"\n── 上个窗口的漂移，这一章补一下 ──\n{feedback}\n" if feedback else ""
    return f"""本章细纲：
{outline}

── 调子 ──
{pb.TONE}

下面是同类作品里几段真东西。不要抄内容，听它的嗓子：

【外部视角开场】
{pb.pick('外部视角开场')}

【反讽旁白·把正经话戳破】
{pb.pick('反讽旁白·把正经话戳破', 4)}

【算自己的账】
{pb.pick('算自己的账')}
{CORE}{fb}
── 形制 ──
正文 2800~3400 字，写满，不许提前收尾。
开场用细纲指定的视角人的眼睛，前 300 字里主角可以不出现。
不许替主角辩护——叙述者可以笑他，不许夸他。
章末不许是感想、总结、决心。
不许出现英文单词，不许用 markdown 标记。

直接输出正文。"""


def drift_line(prev_measures: list) -> str:
    """从上一批的实测算出漂移最大的两项，写成一句人话。"""
    if not prev_measures:
        return ""
    agg = {k: statistics.mean([m[k] for m in prev_measures]) for k in TARGET}
    devs = []
    for k, (lo, hi) in TARGET.items():
        v = agg[k]
        if v < lo:   devs.append((abs(v - lo) / max(lo, 1e-9), k, "低", v))
        elif v > hi: devs.append((abs(v - hi) / max(hi, 1e-9), k, "高", v))
    devs.sort(reverse=True)
    if not devs:
        return ""
    out = []
    for _, k, d, v in devs[:2]:
        out.append(f"· {NAME_CN[k].format(d)}（上个窗口 {v:.2f}，目标 {TARGET[k][0]}~{TARGET[k][1]}）"
                   f"—— 这一章请：{FIX[k][d]}")
    return "\n".join(out) + "\n其余各项保持原样，不要为了补这两项牺牲别的。"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True, help="上一轮闭环目录（复用里面的倒推细纲）")
    ap.add_argument("--model", default="glm-5.3-flash")
    a = ap.parse_args()

    d = Path(a.outdir)
    ols = sorted(d.glob("*_outline.md"))
    assert ols, f"{d} 里没有倒推细纲"
    print(f"复用 {len(ols)} 份倒推细纲 ← {d}\n", flush=True)

    out = ROOT / "reports/window_feedback" / time.strftime("%m%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)

    # 原章与「全量八条」的既有结果，作为对照
    prev = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    orig = [r["原章"] for r in prev if "原章" in r]
    full = [r["重写"] for r in prev if "重写" in r]
    fb = drift_line(full)
    print(f"从「全量八条」那一轮算出的窗口漂移反馈：\n{fb}\n", flush=True)

    def run(f, group, feedback):
        ol = f.read_text(encoding="utf-8")
        t = time.time()
        txt = pb.call(a.model, p_lean(ol, feedback), 9000, 0.92)
        (out / f"{f.stem.split('_')[0]}_{group}.md").write_text(txt, encoding="utf-8")
        m = pb.measure(txt)
        print(f"  [{group}] {f.stem}: {m['字数']}字 对白{m['对白占比']:.2f} 问{m['每千字问号']:.1f} "
              f"叹{m['每千字叹号']:.1f} 段均{m['段均字数']:.0f} 独反问{m['独立反问句']} "
              f"解说{m['解说体']} /{time.time()-t:.0f}s", flush=True)
        return m

    jobs = [(f, "G_精简三条", "") for f in ols] + [(f, "H_精简+窗口反馈", fb) for f in ols]
    res = {}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run, f, g, fbk): g for f, g, fbk in jobs}
        for fu in cf.as_completed(futs):
            res.setdefault(futs[fu], []).append(fu.result())

    ks = ["字数", "对白占比", "每千字问号", "每千字叹号", "段均字数", "独立反问句", "解说体"]
    def avg(ms, k): return statistics.mean([m[k] for m in ms])
    def score(ms):
        return statistics.mean([abs(avg(ms, k) - avg(orig, k)) / max(avg(orig, k), 1e-9) for k in ks])

    print(f"\n{'组':18s}" + "".join(f"{k:>10s}" for k in ks) + "   平均相对差")
    print(f"{'原章':18s}" + "".join(f"{avg(orig,k):10.2f}" for k in ks))
    print(f"{'F_全量八条':18s}" + "".join(f"{avg(full,k):10.2f}" for k in ks) + f"{score(full)*100:11.0f}%")
    for g in ["G_精简三条", "H_精简+窗口反馈"]:
        if g in res:
            print(f"{g:18s}" + "".join(f"{avg(res[g],k):10.2f}" for k in ks) + f"{score(res[g])*100:11.0f}%")
    json.dump({g: res[g] for g in res}, open(out / "summary.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n产物在 {out}")


if __name__ == "__main__":
    main()

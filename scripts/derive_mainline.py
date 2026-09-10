#!/usr/bin/env python3
"""正文 → 细纲 → 主线 → 种子，自底向上真推。

之前的拆解是「我读了书然后写感想」，种子是从印象里编的。
这条流水线把方向反过来：每一级的输入都只能是下一级的**产物**，
最后挖出来的种子如果真是这本书的种子，那它应该能解释全部主线链。

  L1 细纲   等距取样，真章倒推 11 栏（复用 reverse_outline 的提示词）
  L2 主线   分窗：全部章节名 + 窗内倒推细纲 → 该段「解决了什么/解法生出了什么」
            串链：所有窗 → 全书但是链，逐节验证 exposes == 下一节 solves
  L3 种子   主线链 + 开局细纲 → 八项种子（clock/shell/counter_move/cheat/
            prepaid_emotion/accounts/volume_chain/planted_misreads）

  平行三层（同一颗种子的表现层，全方面要求）：
  L2s 文笔   真摘录（不是细纲——细纲里文笔已经被洗掉了）→ 文笔指纹
  L2t 标题   全部章节名 → 标题系统（标题在替作者干什么活）
  L2a 推进   全部倒推细纲的承接/后果/账目/误读四栏 → 逐章推进机制

用法: python3 scripts/derive_mainline.py --book ds
      python3 scripts/derive_mainline.py --book qj --stride 28
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, importlib.util, json, re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("pb", ROOT / "scripts/prompt_bench.py")
pb = importlib.util.module_from_spec(_s); _s.loader.exec_module(pb)
_r = importlib.util.spec_from_file_location("ro", ROOT / "scripts/reverse_outline.py")
ro = importlib.util.module_from_spec(_r); _r.loader.exec_module(ro)

SP = ro.SP
BOOKS = ro.BOOKS


def p_window(name: str, lo: int, hi: int, titles: list[str], outlines: list[str],
             prev_window: str) -> str:
    return f"""你在给长篇小说《{name}》做主线逆向工程。下面是第 {lo}-{hi} 章的材料：
① 这一段**每一章的标题**（标题是作者自己写的路标，别浪费）
② 其中若干章的倒推细纲（11 栏，来自真章正文）
③ 上一段主线的结论（衔接用，第一段为空）

── ① 章节标题 ──
{chr(10).join(titles)}

── ② 倒推细纲（取样章）──
{chr(10).join(outlines)}

── ③ 上一段主线结论 ──
{prev_window or "（这是全书第一段）"}

只根据材料写，不要用你对这本书的记忆。输出 6 栏：

段旗：这一段的主线一句话（谁在解决什么问题）
入口：这一段开场时悬着的那个问题（上一段留下的，第一段写开局危机）
解法：主角/主角方用什么把入口问题按下去了（写具体手段，不写感想）
反噬：**解法本身**生出了什么新问题（不是外部又来了敌人，是这个解法的代价/副作用/它惊动了谁）
账目：这一段里最重要的 1-3 格账，各写成 {{什么}} {{旧}} → {{新}}
错算：这一段里最重要的一次误读（谁把什么看成了什么，因此做了什么），没有就写「无」"""


def p_chain(name: str, windows: list[str]) -> str:
    return f"""下面是《{name}»全书按顺序分段逆推出的主线（每段 6 栏）。
把它们串成**全书主线链**。要求：

1. 链上每一节写成：「问题 → 解法 → 但是（解法生出的新问题）」，
   下一节的「问题」必须就是上一节的「但是」。接不上的地方**明说接不上**，
   不许硬圆——接不上本身就是重要发现。
2. 标出全书的「钟」：一直在倒计时、逼着主角行动的那个外部压力是什么，
   它在链上每一节是变紧了还是变松了。
3. 标出「壳」：主角借来用的那个身份/位置/招牌，它在哪几节被换过。
4. 用一句话写出全书总旗：整本书在讲谁用什么办法对付什么。

── 分段主线 ──
{chr(10).join(f"【第{i+1}段】{chr(10)}{w}" for i, w in enumerate(windows))}"""


def p_style(name: str, excerpts: list[str]) -> str:
    return f"""下面是《{name}》从全书各处等距取的 {len(excerpts)} 段原文摘录。
你是文笔逆向工程师。只根据摘录归纳，禁止用你对作者的记忆。输出 8 栏，
每一栏都必须**引用摘录里的原句**做证据（引 1-2 句，注明第几段）：

句子：句长节奏怎么走（长句干什么用、短句干什么用、什么时候突然断）
对白：人物说话的质感靠什么（称谓、口癖、黑话、身份感怎么从嘴里漏出来）
反讽：作者的坏笑藏在哪（是词、是转折、还是让人物自己出丑）
解说：制度/数字/器物是怎么嵌进叙事的（停下来讲？借谁的嘴？借什么由头）
失态：高位者丢脸的场面怎么写（写到什么程度收笔、旁观者给什么反应）
心理：人物心里的算计怎么呈现（直写内心？借动作？借自言自语？）
段落：段落形态（多长、怎么切、场景切换用什么手法）
狠劲：这个作者最狠的一手是什么（别的网文作者不敢写或想不到写的）

── 摘录 ──
{chr(10).join(f"【第{i+1}段】{e}" for i, e in enumerate(excerpts))}"""


def p_titles(name: str, titles: list[str]) -> str:
    return f"""下面是《{name}》全部 {len(titles)} 个章节标题，按顺序。
你做标题系统逆向工程：作者的标题在替他干什么活？只根据标题列表归纳。输出：

功能分类：把标题按功能分类（每类给名字 + 全书占比估计 + 5 个例子）
信息策略：标题剧透到什么程度？卖什么、藏什么？
人名策略：谁的名字上标题最多？上标题的人此时通常在走运还是倒霉？
连续剧手法：相邻标题怎么接力（上下集、连环问、同一件事换角度）？举 3 组实例
语气：标题的口气是谁的口气（说书人？对手？账房？）用 5 个标题证明
一句话总结：这本书的目录整体在给读者讲一个什么故事

── 标题 ──
{chr(10).join(titles)}"""


def p_advance(name: str, rows: list[str]) -> str:
    return f"""下面是《{name}》等距取样倒推出的细纲里「承接/后果/账目/误读」四栏，按章序。
你研究**剧情推进机制**：这本书一章一章是靠什么往前走的？只根据材料归纳。输出：

承接率：有多少章的开头明确接住上一章结尾？接不上的章在干什么？
推进燃料：往前推的动力里，账目变动/新误读/旧误读发酵/新人物/外部事件 各占几成？
　　　　　每种给 2 个实例（引章号）
误读寿命：一次误读通常发酵几章才被戳破？戳破时通常发生什么？举 2 条完整链
账目节奏：账多少章动一次大的？动账的章和不动账的章交替有什么规律？
钩子类型：「后果」栏里出现的下章输入分几类？各占几成？
一句话总结：这本书的推进公式

── 材料 ──
{chr(10).join(rows)}"""


def p_seed(name: str, chain: str, first_outlines: list[str]) -> str:
    return f"""你拿到了《{name}》的全书主线链和开局几章的倒推细纲。
现在把它压缩回**种子**——如果作者动笔前只写一页纸，这页纸上是什么。

规矩：种子里的每一项都必须能在主线链里指认出对应物；指认不出来就别写。
输出严格按下面 8 项：

clock（钟）：开局就在倒计时的外部压力。写：什么在逼近 + 大约多少时间 + 到点会怎样
shell（壳）：主角借用的身份/位置。写：壳是什么 + 壳给他什么 + 壳随时会因为什么被收走
counter_move（反手）：主角对付钟的第一步棋，必须是**反常识**的那一步（正常人不会这么走）
cheat（金手指）：主角比本地人多什么 + 这个优势的**盲区**（它在什么情况下失灵）
prepaid_emotion（预付情绪）：开局先让读者恨上谁/憋着哪口气，后面全书慢慢兑付
accounts（账本）：全书反复动的那几格账（3-5 格），各写清计量单位
volume_chain（但是链）：卷级链条，每节「解决X，但是解法暴露Y」，4-8 节
planted_misreads（预埋误读）：开局埋下的、注定被各方看错的那 2-3 件事（谁会看错、错成什么）

── 主线链 ──
{chain}

── 开局细纲 ──
{chr(10).join(first_outlines)}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="ds", choices=list(BOOKS))
    ap.add_argument("--stride", type=int, default=0, help="0=自动(目标80份细纲)")
    ap.add_argument("--windows", type=int, default=0, help="0=自动(每窗约70章)")
    ap.add_argument("--m-outline", default="glm-5.3-flash")
    ap.add_argument("--m-main", default="qwen3.8-max")
    a = ap.parse_args()

    f, pat, name = BOOKS[a.book]
    chs = json.loads((SP / f).read_text(encoding="utf-8"))
    N = len(chs)
    stride = a.stride or max(1, round(N / 80))
    nwin = a.windows or max(4, round(N / 70))
    out = ROOT / "reports/mainline" / f"{a.book}-{time.strftime('%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"《{name}》{N} 章 | 细纲取样步长 {stride} | {nwin} 窗 → {out}", flush=True)

    def body_of(n):
        p = SP / (pat % n)
        if not p.exists():
            return ""
        return "\n".join(p.read_text(encoding="utf-8").split("\n")[2:]).strip()

    # ── L1 细纲 ──
    picked = [c for c in chs if c["n"] % stride == 1 or stride == 1]
    picked = [c for c in picked if c["len"] > 1500]
    print(f"[L1] 倒推 {len(picked)} 章细纲", flush=True)
    t0 = time.time()

    def one(c):
        n = c["n"]
        body = body_of(n)
        if not body:
            return None
        prev = body_of(n - 1)
        try:
            ol = pb.call(a.m_outline, ro.p_reverse(prev, body[:9000]), 3000, 0.4)
        except Exception as e:
            print(f"  第{n}章 倒推失败 {type(e).__name__}", flush=True)
            return None
        (out / f"L1_{n:04d}.md").write_text(ol, encoding="utf-8")
        return {"n": n, "title": c["title"], "outline": ol}

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        l1 = [r for r in ex.map(one, picked) if r]
    l1.sort(key=lambda r: r["n"])
    print(f"[L1] 完成 {len(l1)}/{len(picked)} 份 / {time.time()-t0:.0f}s", flush=True)

    # ── L2 主线：分窗（顺序依赖，串行）──
    bounds = [round(i * N / nwin) + 1 for i in range(nwin)] + [N + 1]
    windows = []
    for i in range(nwin):
        lo, hi = bounds[i], bounds[i + 1] - 1
        titles = [c["title"] for c in chs if lo <= c["n"] <= hi]
        ols = [f"◆ 第{r['n']}章\n{r['outline']}" for r in l1 if lo <= r["n"] <= hi]
        t = time.time()
        w = pb.call(a.m_main, p_window(name, lo, hi, titles, ols,
                                       windows[-1] if windows else ""), 2500, 0.4)
        windows.append(w)
        (out / f"L2_win{i+1:02d}_{lo}-{hi}.md").write_text(w, encoding="utf-8")
        print(f"[L2] 窗{i+1}/{nwin} 第{lo}-{hi}章 {time.time()-t:.0f}s", flush=True)

    # ── L2 主线：串链 ──
    t = time.time()
    chain = pb.call(a.m_main, p_chain(name, windows), 6000, 0.4)
    (out / "L2_chain.md").write_text(chain, encoding="utf-8")
    print(f"[L2] 全书主线链 {time.time()-t:.0f}s", flush=True)

    # ── L2s 文笔（喂真摘录）──
    t = time.time()
    ex_ns = [chs[round(i * (N - 1) / 11)]["n"] for i in range(12)]
    excerpts = []
    for n in ex_ns:
        b = body_of(n)
        if len(b) > 2200:
            mid = len(b) // 3
            excerpts.append(f"(第{n}章) " + b[mid:mid + 1800])
    style = pb.call(a.m_main, p_style(name, excerpts), 4000, 0.4)
    (out / "L2s_style.md").write_text(style, encoding="utf-8")
    print(f"[L2s] 文笔指纹（{len(excerpts)} 段摘录）{time.time()-t:.0f}s", flush=True)

    # ── L2t 标题系统 ──
    t = time.time()
    all_titles = [c["title"] for c in chs]
    tit = pb.call(a.m_main, p_titles(name, all_titles), 4000, 0.4)
    (out / "L2t_titles.md").write_text(tit, encoding="utf-8")
    print(f"[L2t] 标题系统（{len(all_titles)} 个）{time.time()-t:.0f}s", flush=True)

    # ── L2a 推进机制 ──
    t = time.time()
    rows = []
    for r in l1:
        keep = []
        for fld in ("承接", "后果", "账目", "误读"):
            m = re.search(rf"^\s*{fld}\s*[:：](.*)$", r["outline"], re.M)
            if m:
                keep.append(f"{fld}={m.group(1).strip()[:110]}")
        rows.append(f"第{r['n']}章 | " + " | ".join(keep))
    adv = pb.call(a.m_main, p_advance(name, rows), 4000, 0.4)
    (out / "L2a_advance.md").write_text(adv, encoding="utf-8")
    print(f"[L2a] 推进机制 {time.time()-t:.0f}s", flush=True)

    # ── L3 种子 ──
    t = time.time()
    first = [f"◆ 第{r['n']}章\n{r['outline']}" for r in l1[:4]]
    seed = pb.call(a.m_main, p_seed(name, chain, first), 4000, 0.4)
    (out / "L3_seed.md").write_text(seed, encoding="utf-8")
    print(f"[L3] 种子 {time.time()-t:.0f}s", flush=True)
    print(f"\n全部产物在 {out}", flush=True)


if __name__ == "__main__":
    main()

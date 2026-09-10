#!/usr/bin/env python3
"""细纲反推与闭环校验。

正文层我用原作真章校准过闸门；细纲层从来没跟任何真东西对过——只是一张我写的字段表。
这个脚本补上：

  第一步 倒推：拿原作真章 → 让模型写出「能生成这一章的细纲」
                统计每个字段的填充率。填不出来的字段，就是我编的。
  第二步 闭环：把倒推出的细纲喂回正文提示词 → 生成正文' → 与原章逐项对比。
                这是比合成细纲严格得多的测试。

用法:
  python3 scripts/reverse_outline.py --book ds --n 8 --model glm-5.3-flash
  python3 scripts/reverse_outline.py --book qj --n 8 --closeloop
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, importlib.util, json, random, re, statistics, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("pb", ROOT / "scripts/prompt_bench.py")
pb = importlib.util.module_from_spec(_s); _s.loader.exec_module(pb)

SP = Path("/tmp/claude-1000/-opt-AI-automatically-generates-novels/"
          "c40d4bce-e75c-4ae0-91c9-15d8ced735b7/scratchpad")
BOOKS = {"ds": ("chapters.json", "ch_s/%03d.txt", "大宋有种"),
         "qj": ("qj_chapters.json", "qj/%04d.txt", "抢救大明朝")}

FIELDS = ["标题", "视角", "承接", "动作", "误读", "后果", "账目", "代价",
          "解说", "重场", "章末"]


def p_reverse(prev: str, body: str) -> str:
    return f"""下面是一部长篇历史小说里连续的两章。你的任务是**倒推**：
写出一份细纲，使得一个作者拿着这份细纲，能写出第二章这样的正文。

这是**逆向工程**，不是读后感。只写正文里**真实存在**的东西。
**如果某一栏在正文里根本没有对应的内容，就写「无」。不要为了填满而编。**

── 上一章（只给结尾，用来判断「承接」）──
{prev[-700:]}

── 本章正文 ──
{body}

── 请输出细纲，严格按下面 11 栏 ──

标题：正文原有的标题（照抄）
视角：本章主要从谁的眼睛看？他此刻的处境、他今天最怕什么、他今天想要什么
承接：上一章结尾那条信息，在本章第几段落地？落地成了什么？（没有承接就写「无」）
动作：主角在本章做的那一个动作（动词开头一句话）。主角没出场就写「主角未出场」
误读：本章里有没有人对某件事**推断错了**？写：谁 + 他凭什么这么想 + 他得出的错误结论 + 他因此做了什么。没有就写「无」
后果：本章的事生出了什么新麻烦（下一章的输入）？没有就写「无」
账目：本章有没有某个可数的状态发生了变化（兵力/地盘/名分/钱/技术/制度/人手）？
　　　写成 {{什么}} from {{旧}} → to {{新}}。没有就写「本章不动账」
代价：本章有没有谁付出了可查的损失（谁受损、损多少、能不能恢复）？没有就写「无」
解说：本章有没有一段把某条制度/数字/器物讲透？写那一条是什么。没有就写「无」
重场：哪一段是本章的重心
章末：正文最后落在什么上？（一条新消息／一个新名字／一句话／一个疑问）照实写

只输出这 11 栏，不要评论、不要分析写法。"""


def p_draft_from(outline: str) -> str:
    return pb.p_draft(outline)


def fill_rate(text: str) -> dict:
    out = {}
    for f in FIELDS:
        m = re.search(rf"^\s*{f}\s*[:：](.*?)(?=\n\s*(?:{'|'.join(FIELDS)})\s*[:：]|\Z)",
                      text, re.S | re.M)
        v = (m.group(1).strip() if m else "")
        empty = (not v) or re.fullmatch(r"[无没有／/\-—。．\s]*", v) or v.startswith("本章不动账") \
                or v.startswith("主角未出场")
        out[f] = {"有": not empty, "字数": len(v), "值": v[:160]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="ds", choices=list(BOOKS))
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--closeloop", action="store_true", help="把倒推的细纲喂回正文提示词并与原章对比")
    ap.add_argument("--seed", type=int, default=17)
    a = ap.parse_args()

    f, pat, name = BOOKS[a.book]
    chs = [c for c in json.loads((SP / f).read_text(encoding="utf-8")) if c["len"] > 1800]
    random.seed(a.seed)
    picked = random.sample(chs[3:], a.n)
    out = ROOT / "reports/reverse_outline" / f"{a.book}-{a.model}-{time.strftime('%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"倒推 {name} 的 {a.n} 章 → {out}\n", flush=True)

    def body_of(n):
        return "\n".join((SP / (pat % n)).read_text(encoding="utf-8").split("\n")[2:]).strip()

    def one(c):
        n = c["n"]
        body = body_of(n)
        prev = body_of(n - 1) if n > 1 else ""
        t = time.time()
        ol = pb.call(a.model, p_reverse(prev, body), 4000, 0.4)
        (out / f"{n:04d}_outline.md").write_text(ol, encoding="utf-8")
        fr = fill_rate(ol)
        rec = {"n": n, "title": c["title"], "fill": fr, "sec": round(time.time() - t)}
        print(f"  第{n}章 倒推完成 {time.time()-t:.0f}s | 填充: "
              + "".join("✓" if fr[x]["有"] else "×" for x in FIELDS), flush=True)
        if a.closeloop:
            t2 = time.time()
            draft = pb.call(a.model, p_draft_from(ol), 9000, 0.92)
            (out / f"{n:04d}_redraft.md").write_text(draft, encoding="utf-8")
            rec["原章"] = pb.measure(body)
            rec["重写"] = pb.measure(draft)
            print(f"  第{n}章 闭环重写 {time.time()-t2:.0f}s", flush=True)
        return rec

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        recs = list(ex.map(one, picked))

    (out / "summary.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1),
                                      encoding="utf-8")

    print(f"\n════ 字段填充率（{name}，{len(recs)} 章）════")
    print("填不出来的字段 = 原作里本来就没有 = 我编的，应该从必填改成选填")
    for fld in FIELDS:
        hit = sum(1 for r in recs if r["fill"][fld]["有"])
        avg = statistics.mean([r["fill"][fld]["字数"] for r in recs])
        bar = "█" * round(hit / len(recs) * 20)
        print(f"  {fld:4s} {hit:2d}/{len(recs)} {hit/len(recs)*100:5.0f}%  均{avg:5.0f}字  {bar}")

    if a.closeloop:
        print(f"\n════ 闭环：原章 vs 用倒推细纲重写 ════")
        ks = ["字数", "对白占比", "每千字问号", "每千字叹号", "段均字数",
              "独立反问句", "误读推断", "解说体"]
        print(f"{'':6s}" + "".join(f"{k:>10s}" for k in ks))
        for tag in ("原章", "重写"):
            av = [statistics.mean([r[tag][k] for r in recs]) for k in ks]
            print(f"{tag:6s}" + "".join(f"{v:10.2f}" for v in av))
    print(f"\n产物在 {out}")


if __name__ == "__main__":
    main()

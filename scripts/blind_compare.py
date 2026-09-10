#!/usr/bin/env python3
"""盲测对比 —— 把原作真章和自己写的章混在一起打乱，让评委在不知道哪是哪的前提下打分。

指标只能量出「像不像」的表层。真正要回答的是「读起来是不是那个东西」，
而这个问题一旦让评委知道哪篇是 AI 写的，答案就不作数了。所以：
  1. 从两本原作里随机抽 K 章（随机选哪一本，也随机选哪一章）
  2. 从指定项目里随机抽 K 章
  3. 打乱、只标 A/B/C…、逐篇独立送评（不让评委看见其它篇，避免互相锚定）
  4. 揭盲后按来源汇总

    python3 scripts/blind_compare.py --project 老辣调端到端验证 --k 3
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import importlib.util                                          # noqa: E402
_s = importlib.util.spec_from_file_location("pb", ROOT / "scripts/prompt_bench.py")
pb = importlib.util.module_from_spec(_s); _s.loader.exec_module(pb)

SP = Path("/tmp/claude-1000/-opt-AI-automatically-generates-novels/"
          "c40d4bce-e75c-4ae0-91c9-15d8ced735b7/scratchpad")
BOOKS = {"大宋有种": ("chapters.json", "ch_s/%03d.txt"),
         "抢救大明朝": ("qj_chapters.json", "qj/%04d.txt")}

RUBRIC = """你在给一部长篇通俗历史小说的单章打分。只看这一章，不要联想别的作品。

按下面六项各打 1~5 分（1=完全没有，5=非常突出），每项给一句话理由，引一处原文。

1. 当众失态：有没有**有身份的人**（官家/宰执/大将/名儒/掌印太监）当着别人的面掉链子，
   而且是用台词和动作演出来的，不是用「他心里一紧」这种叙述带过的？
2. 有人算错：有没有人对眼前发生的事**推断错了**，并且照着错的判断行动？
3. 把规矩讲透：有没有一段把一条制度／数字／器物讲到读者能复述的程度？
4. 叙述者戳破：写完正经话之后，有没有用一句大白话把它的难看真相说出来？
5. 温度：读起来是热的还是冷的？（热=人物在着急、在喊、在出丑；冷=人人克制、含蓄、有威压）
6. 各算各的账：配角开口时，读者知不知道这句话对他自己有什么好处或坏处？

最后再答两个问题：
判断：这一章你觉得是**人写的**还是**AI 写的**？（只答「人」或「AI」，再加一句最主要的理由）
一句话总评：这一章最像什么？

严格按下面格式输出，不要别的：
当众失态：分数|理由|引文
有人算错：分数|理由|引文
把规矩讲透：分数|理由|引文
叙述者戳破：分数|理由|引文
温度：分数|理由|引文
各算各的账：分数|理由|引文
判断：人或AI|理由
总评：一句话
"""

FIELDS = ["当众失态", "有人算错", "把规矩讲透", "叙述者戳破", "温度", "各算各的账"]


def parse(out: str):
    d = {}
    for f in FIELDS:
        m = re.search(rf"^{f}\s*[:：]\s*(\d)", out, re.M)
        d[f] = int(m.group(1)) if m else 0
    m = re.search(r"^判断\s*[:：]\s*(\S+?)[|｜]", out, re.M)
    d["判断"] = "AI" if (m and "AI" in m.group(1).upper()) else ("人" if m else "?")
    m = re.search(r"^总评\s*[:：]\s*(.+)$", out, re.M)
    d["总评"] = m.group(1).strip()[:70] if m else ""
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--k", type=int, default=3, help="每一侧抽几章")
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--from", dest="frm", type=int, default=1)
    a = ap.parse_args()
    random.seed(a.seed or int(time.time()) % 100000)

    pool = []
    for bk, (f, pat) in BOOKS.items():                 # 随机选哪一本、随机选哪一章
        chs = [c for c in json.loads((SP / f).read_text(encoding="utf-8"))
               if 1800 < c["len"] < 4200]
        for c in random.sample(chs, a.k):
            body = "\n".join((SP / (pat % c["n"])).read_text(encoding="utf-8")
                             .split("\n")[2:]).strip()
            pool.append((bk, f"第{c['n']}章", body))
    fs = [x for x in sorted(glob.glob(str(ROOT / "projects" / a.project / "chapters" / "*.md")))
          if int(Path(x).stem) >= a.frm]
    for f in random.sample(fs, min(a.k, len(fs))):
        pool.append(("我生成的", Path(f).stem, Path(f).read_text(encoding="utf-8").strip()))

    random.shuffle(pool)
    labels = [chr(ord("A") + i) for i in range(len(pool))]
    out = ROOT / "reports/blind_compare" / time.strftime("%m%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)
    print(f"盲测 {len(pool)} 篇（每篇独立送评，评委看不到其它篇）→ {out}\n", flush=True)

    def judge(i):
        src, cid, body = pool[i]
        r = pb.call(a.model, RUBRIC + "\n── 待评章节 ──\n" + body[:9000], 2500, 0.3)
        (out / f"{labels[i]}.md").write_text(r, encoding="utf-8")
        d = parse(r)
        print(f"  {labels[i]}：{'／'.join(str(d[x]) for x in FIELDS)}"
              f"　判断={d['判断']}　{d['总评'][:34]}", flush=True)
        return src, cid, d

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(judge, range(len(pool))))

    print("\n════ 揭盲 ════")
    print(f"{'':10s}" + "".join(f"{f[:4]:>7s}" for f in FIELDS) + f"{'均分':>7s}{'被判AI':>8s}")
    for src in ["大宋有种", "抢救大明朝", "我生成的"]:
        rs = [d for s, _, d in res if s == src]
        if not rs:
            continue
        av = [sum(d[f] for d in rs) / len(rs) for f in FIELDS]
        ai = sum(1 for d in rs if d["判断"] == "AI")
        print(f"{src:10s}" + "".join(f"{v:7.1f}" for v in av)
              + f"{sum(av)/len(av):7.2f}{ai}/{len(rs):>7s}".replace(" ", " "))
    print("\n逐篇（揭盲后）：")
    for (src, cid, d), lb in zip(res, labels):
        print(f"  {lb}  {src:7s} {cid:8s} 均{sum(d[f] for f in FIELDS)/6:.1f} "
              f"判断={d['判断']}  {d['总评'][:44]}")
    json.dump([{"label": l, "src": s, "id": c, **d} for (s, c, d), l in zip(res, labels)],
              open(out / "summary.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n产物在 {out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""种子 → 合同树。逐层拆，每层拆完立刻用程序验，验不过就打回去重修。

用法:
  python3 scripts/build_tree.py --seed <种子json> --chapters 345 --out <目录>
  python3 scripts/build_tree.py --resume <目录>        # 接着往下拆
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import tree as tr                                    # noqa: E402
from server.orchestrator import call                             # noqa: E402

#: 每层拆成几块。7 阶段是用户定的；往下按章数自适应。
FANOUT = {"book": 7, "stage": 3, "volume": 3, "unit": 0}   # unit=0 → 按章数全展开


def say(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def mk_call(profile="planning", mt=6000):
    def _c(prompt: str) -> str:
        r = call(profile, prompt, max_tokens=mt)
        return (r.text or "").strip()
    return _c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed"); ap.add_argument("--chapters", type=int, default=345)
    ap.add_argument("--out", default=""); ap.add_argument("--resume", default="")
    ap.add_argument("--max-level", default="unit",
                    help="拆到哪一层为止（章合同由写作阶段逐章生成）")
    a = ap.parse_args()

    out = Path(a.resume or a.out)
    out.mkdir(parents=True, exist_ok=True)
    f_tree = out / "tree.json"
    nodes = {}
    if f_tree.exists():
        nodes = {k: tr.Node.from_dict(v)
                 for k, v in json.loads(f_tree.read_text(encoding="utf-8")).items()}
        say(f"续拆：已有 {len(nodes)} 个节点")

    def save():
        f_tree.write_text(json.dumps({k: v.to_dict() for k, v in nodes.items()},
                                     ensure_ascii=False, indent=1), encoding="utf-8")

    c = mk_call()
    if "R" not in nodes:
        seed = json.loads(Path(a.seed).read_text(encoding="utf-8"))
        txt = "\n\n".join(f"【{k}】\n{v}" for k, v in seed.items() if v)
        say(f"种子 {len(txt)} 字 → 根节点合同")
        for i in range(3):
            raw = c(tr.p_root(txt, a.chapters))
            root = tr.parse_root(raw, a.chapters)
            if root and (root.entry.hero or root.entry.facts):
                nodes["R"] = root
                save()
                say(f"根节点：《{root.title}》{root.line[:50]}")
                break
            # 失败要说清是哪种失败 —— 「没解析出来」把「模型返回空」和
            # 「返回了但不是 JSON」混成一句, 查不出真因(实测前两次都是空输出)。
            why = ("模型返回空" if not raw.strip()
                   else "没找到 JSON" if "{" not in raw
                   else "JSON 解析失败" if root is None
                   else "解析出来了但合同是空的")
            say(f"  根合同第 {i+1} 次失败：{why}（收到 {len(raw)} 字）")
        else:
            say("根节点建不出来，退出"); return 1

    order = ["book", "stage", "volume", "unit"]
    stop = order.index(a.max_level) if a.max_level in order else 3
    for lvl in order[:stop + 1]:
        todo = [n for n in nodes.values() if n.level == lvl and not n.children]
        if not todo:
            continue
        say(f"── 拆 {lvl} 层，{len(todo)} 个节点 ──")
        for nd in sorted(todo, key=lambda x: x.start):
            sibs = sorted([m for m in nodes.values()
                           if m.id.rsplit(".", 1)[0] == nd.id.rsplit(".", 1)[0]
                           and m.id != nd.id and m.level == nd.level],
                          key=lambda x: x.start)
            left = next((s for s in sibs if s.end < nd.start), None)
            right = next((s for s in sibs if s.start > nd.end), None)
            parent = nodes.get(nd.id.rsplit(".", 1)[0]) if "." in nd.id else None
            k = FANOUT.get(lvl) or max(2, round((nd.end - nd.start + 1) / 8))
            k = max(2, min(k, nd.end - nd.start + 1))
            t = time.time()
            kids, errs = tr.decompose(nd, parent, left, right, k, c, log=say)
            if not kids:
                say(f"  {nd.id} 拆失败：{errs[:2]}"); continue
            for kid in kids:
                nodes[kid.id] = kid
            nd.children = [x.id for x in kids]
            save()
            flag = "✓" if not errs else f"⚠{len(errs)}处未消"
            say(f"  {nd.id}「{nd.title[:12]}」→ {len(kids)} 块 {flag} "
                f"{time.time()-t:.0f}s")
    errs = tr.audit_tree(nodes)
    say(f"全树 {len(nodes)} 节点，体检 {len(errs)} 处违约")
    for e in errs[:10]:
        say("   · " + e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

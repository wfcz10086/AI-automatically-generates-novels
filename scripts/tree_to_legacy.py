#!/usr/bin/env python3
"""把合同树桥接成旧管线吃的 outline.md / volumes.json。

写作管线(排纲/正文/评审)已经打磨了几百个修复, 不推倒重建 —— 树只接管
「写什么」的供料层: 总纲=里程碑链渲染, 卷=里程碑。排纲时再由 milestone_ctx
注入本节合同(见 orchestrator)。

用法: python3 scripts/tree_to_legacy.py 金钟镇天下
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import tree as tr                     # noqa: E402
from server.orchestrator import Project           # noqa: E402


def main():
    p = Project(sys.argv[1])
    nodes = {k: tr.Node.from_dict(v)
             for k, v in json.loads((p.dir / "tree.json").read_text(encoding="utf-8")).items()}
    root = nodes["R"]
    ms = sorted((n for n in nodes.values() if n.id != "R"), key=lambda x: x.start)

    # volumes.json ← 里程碑(含但是链两栏, 旧的 _check_volume_chain 兼容)
    vols = []
    for i, m in enumerate(ms):
        vols.append({
            "index": i + 1, "name": m.title or f"第{i+1}节",
            "start": m.start, "end": m.end,
            "solves": m.solves, "exposes": m.exposes,
            "text": (f"卷名：{m.title}\n章节范围：第{m.start}章-第{m.end}章\n"
                     f"本卷解决：{m.solves}\n解决之后暴露：{m.exposes}\n"
                     f"关键错算：{m.line}\n"
                     f"卷末账本：" + "；".join(f"{k}={v}" for k, v in m.exit.accounts.items())),
        })
    p.write("volumes.json", json.dumps(vols, ensure_ascii=False, indent=2))

    # outline.md ← 根合同 + 链
    lines = [f"# {root.title}", "", f"**全书一句话**：{root.line}", "",
             "## 开局", root.entry.brief(800), "",
             "## 终局", root.exit.brief(800), "",
             "## 推进链（每节：解决 → 但是）"]
    for i, m in enumerate(ms):
        lines.append(f"{i+1}. 【{m.title}】解决：{m.solves}｜但是：{m.exposes}")
    p.write("outline.md", "\n".join(lines))
    print(f"桥接完成: volumes.json {len(vols)} 卷, outline.md {sum(len(x) for x in lines)} 字")


if __name__ == "__main__":
    main()

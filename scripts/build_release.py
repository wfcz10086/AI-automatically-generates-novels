#!/usr/bin/env python3
"""构建发行版：python3 scripts/build_release.py <书名> <spec> [起章] [止章]"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.orchestrator import Project, Novelist, call, clean, slugify
from server.release import SPECS, compile_chapter

title, spec_id = sys.argv[1], sys.argv[2]
lo_ch = int(sys.argv[3]) if len(sys.argv) > 3 else 1
hi_ch = int(sys.argv[4]) if len(sys.argv) > 4 else 10**6
p = Project(slugify(title)); spec = SPECS[spec_id]
out_dir = p.dir / "release" / spec_id
out_dir.mkdir(parents=True, exist_ok=True)
state_f = out_dir / "_state.json"
st = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else {"done": [], "next_no": 1, "toc": []}
llm = lambda q: clean(call("polishing", q, max_tokens=1500).text)
ol = p._load("chapter_outlines.json", {})
done_src = sorted(p.state.get("done", []))
for n in done_src:
    if n < lo_ch or n > hi_ch or n in st["done"]:
        continue
    text = p.chapter(n)
    m = re.search(r"第\s*\d+\s*章\s*(.+)", ol.get(str(n), ""))
    hint = m.group(1).strip().splitlines()[0][:20] if m else f"第{n}章"
    try:
        segs = compile_chapter(n, hint, text, spec, llm)
    except Exception as e:
        print(f"[!] 源第{n}章失败: {e}"); continue
    for s_ in segs:
        no = st["next_no"]
        (out_dir / f"{no:03d}.md").write_text(s_["text"], encoding="utf-8")
        st["toc"].append({"no": no, "title": s_["title"], "src": n,
                          "cn": len(re.findall(r"[一-鿿]", s_["text"]))})
        st["next_no"] += 1
    st["done"].append(n)
    state_f.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"源第{n:3d}章 -> 发行 {len(segs)} 章 (累计 {st['next_no']-1})", flush=True)
print(f"\n完成: 源 {len(st['done'])} 章 -> 发行版 {st['next_no']-1} 章")

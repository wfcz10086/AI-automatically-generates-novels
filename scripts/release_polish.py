#!/usr/bin/env python3
"""发行版后处理：并碎章 / 切超标 / 补钩。python3 scripts/release_polish.py <书名> <spec>"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.orchestrator import Project, call, clean, slugify

title, spec_id = sys.argv[1], sys.argv[2]
p = Project(slugify(title))
d = p.dir / "release" / spec_id
st = json.loads((d / "_state.json").read_text(encoding="utf-8"))
toc = st["toc"]
texts = {t["no"]: (d / f"{t['no']:03d}.md").read_text(encoding="utf-8") for t in toc}
cn = lambda s: len(re.findall(r"[一-鿿]", s))

# ① 并碎章: <1500 字并入前一章（同源优先）
merged, out = set(), []
for i, t in enumerate(toc):
    if t["no"] in merged:
        continue
    body = texts[t["no"]]
    while cn(body) < 1500 and i + 1 < len(toc) and toc[i+1]["no"] not in merged:
        nxt = toc[i+1]
        body += "\n\n" + texts[nxt["no"]]
        merged.add(nxt["no"]); i += 1
    out.append({"title": t["title"], "body": body, "src": t["src"]})
print(f"并碎章: {len(toc)} -> {len(out)}")

# ② 切超标: >3400 字按段落对半
final = []
for t in out:
    b = t["body"]
    if cn(b) > 3400:
        paras = b.split("\n"); half, acc, cut = cn(b)/2, 0, len(paras)//2
        for j, para in enumerate(paras):
            acc += cn(para)
            if acc >= half: cut = j+1; break
        final.append({**t, "body": "\n".join(paras[:cut]).strip()})
        final.append({"title": t["title"] + "·续", "body": "\n".join(paras[cut:]).strip(), "src": t["src"]})
    else:
        final.append(t)
print(f"切超标后: {len(final)} 章")

# ③ 补钩: 结尾平收的章，模型补 1-2 句（批量，每批 8 章一调用）
HOOK = re.compile(r'(?:等着|走着瞧|来了|变天|突然|短信|电话|响起|推开门|出现|杀机|阴影|盯上|风暴|倒计时|没想到|变数|危机|而此刻|谁也没|眯起|冷笑|攥紧)')
flat_idx = [i for i, t in enumerate(final)
            if not (HOOK.search(t["body"].strip()[-150:]) or
                    t["body"].strip().rstrip().endswith(('？','！','……')))]
print(f"平收章: {len(flat_idx)}")
for k in range(0, len(flat_idx), 8):
    batch = flat_idx[k:k+8]
    listing = "\n".join(f"【{j}】…{final[i]['body'].strip()[-110:]}" for j, i in enumerate(batch))
    raw = clean(call("polishing",
        "以下是若干章小说的结尾，全部平收。为每条写 1-2 句章末钩子，衔接原文语气，"
        "制造必看下一章的悬念（新威胁/未接的消息/反常细节/倒计时）。"
        "禁止「才刚刚开始」「好戏在后头」这类万金油。\n"
        "只输出 JSON：{\"hooks\":[\"钩子0\",\"钩子1\",…]}\n\n" + listing,
        max_tokens=900).text)
    m = re.search(r"\{.*\}", raw, re.S)
    hooks = (json.loads(m.group(0)).get("hooks", []) if m else [])
    for j, i in enumerate(batch):
        if j < len(hooks) and hooks[j].strip():
            final[i]["body"] += "\n\n" + hooks[j].strip()
    print(f"  补钩 {min(k+8,len(flat_idx))}/{len(flat_idx)}", flush=True)

# 落盘（重编号）
for f in d.glob("[0-9]*.md"): f.unlink()
new_toc = []
for no, t in enumerate(final, 1):
    (d / f"{no:03d}.md").write_text(t["body"], encoding="utf-8")
    new_toc.append({"no": no, "title": t["title"], "src": t["src"], "cn": cn(t["body"])})
st["toc"] = new_toc; st["next_no"] = len(final) + 1
(d / "_state.json").write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
lens = [t["cn"] for t in new_toc]
print(f"\n最终: {len(final)} 章  中位 {sorted(lens)[len(lens)//2]}  区间 {min(lens)}-{max(lens)}")

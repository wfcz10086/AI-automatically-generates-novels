#!/usr/bin/env python3
"""扫文风包与全局纪律之间的互相打架。

提示词里同一件事被好几个地方各说一遍, 说法还不一样 —— 模型只能各取一半,
实测表现就是「文体漂移 27%」「剧情太标」。这些冲突靠肉眼看不出来, 因为它们
分散在: 包的 manner / sentenceChars / paragraphChars / descriptionBudget、
手艺块的自然语言、config 的 anti_ai_rules、编译器里写死的句子。

用法: python3 scripts/pack_conflicts.py
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_global() -> list:
    import yaml
    d = yaml.safe_load((ROOT / "config/settings.yaml").read_text(encoding="utf-8"))
    out = []
    for k in ("anti_ai_rules", "chapter_directives", "character_rules"):
        v = d.get(k)
        if isinstance(v, list):
            out += [str(x) for x in v]
    return out


def nums(text: str, pat: str):
    return [int(x) for x in re.findall(pat, text)]


def check(pack: dict, glob: list) -> list:
    """返回 [(严重度, 议题, 说明)]"""
    out = []
    name = pack.get("id", "?")
    blob_pack = json.dumps(pack, ensure_ascii=False)
    gtext = "\n".join(glob)

    # ① 句子: manner 说短句，sentenceChars 却要求别切碎
    sc = pack.get("sentenceChars")
    manner = str(pack.get("manner") or "")
    if sc and len(sc) == 2 and re.search(r"短句", manner):
        if int(sc[0]) >= 16:
            out.append(("高", "句子长短",
                        f"manner 写「{manner[:14]}…」要短句，但 sentenceChars="
                        f"{sc} 会发出「句子不要切碎…不许写成一句七八个字」。"
                        f"模型同时收到两条相反指令。"))

    # ② 段落: paragraphChars 限制单句段，手艺块却要「大量单句成段」
    pc = pack.get("paragraphChars")
    if pc and len(pc) == 2:
        seg = str((pack.get("段落与转场") or {}).get("段落", ""))
        if re.search(r"大量单句成段|几个字就是一个自然段", seg):
            out.append(("高", "段落长短",
                        f"paragraphChars={pc} 会发出「每段两到三句、"
                        f"单句段一章不超过八个」，而「段落与转场」写着"
                        f"「大量单句成段」—— 两条对着干。"))
        if re.search(r"一段只写一个动作|段落短", gtext) and int(pc[1]) >= 40:
            out.append(("中", "段落长短",
                        f"全局纪律里有「一段只写一个动作或一句话」，"
                        f"而本包 paragraphChars={pc} 要的是两三句一段。"))

    # ③ 同一个数字在两处不一样
    db = json.dumps(pack.get("descriptionBudget") or {}, ensure_ascii=False)
    for label, pat in (("环境描写", r"环境描写[^0-9]{0,8}(\d+)"),
                       ("情绪形容词", r"情绪形容词[^0-9]{0,8}(\d+)"),
                       ("比喻", r"比喻[^0-9]{0,8}(\d+)")):
        a, b = nums(db, pat), nums(gtext, pat)
        if a and b and set(a) != set(b):
            out.append(("中", label,
                        f"文风包说 {a}，全局纪律说 {b} —— 同一个限额两个数。"))

    # ④ 结尾: 同一件事被几处各说一遍
    ends = []
    if re.search(r"章末不许是感想|章末不许", blob_pack):
        ends.append("包.写作守则")
    if re.search(r"不要在结尾进行总结", gtext + blob_pack):
        ends.append("全局/编译器")
    if re.search(r"禁用万金油结尾", gtext):
        ends.append("全局.万金油结尾")
    if len(ends) > 2:
        out.append(("低", "结尾写法",
                    f"同一条规矩在 {len(ends)} 处各说一遍（{'、'.join(ends)}）—— "
                    f"不矛盾但稀释优先级。"))
    return [(lvl, name, ax, msg) for lvl, ax, msg in out]


def main():
    glob = load_global()
    packs = sorted((ROOT / "packs/style").glob("*.json"))
    rows = []
    for f in packs:
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[跳过] {f.name}: {e}")
            continue
        rows += check(p, glob)
    if not rows:
        print("没扫出冲突。")
        return 0
    order = {"高": 0, "中": 1, "低": 2}
    rows.sort(key=lambda r: (order.get(r[0], 9), r[1]))
    cur = None
    for lvl, pk, ax, msg in rows:
        if pk != cur:
            print(f"\n══ {pk} ══")
            cur = pk
        print(f"  [{lvl}] {ax}：{msg}")
    print(f"\n共 {len(rows)} 处，其中高 {sum(1 for r in rows if r[0]=='高')} 处。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

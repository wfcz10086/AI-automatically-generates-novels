#!/usr/bin/env python3
"""按体检结论修复已写章节。

三类问题三种修法，都走框架，不手改文本：
  · 格式类（markdown 标题、引号风格）—— 确定性，normalize_body() 直接改
  · 语义类（英文残留、数字矛盾）—— 换成什么是判断题，交给模型定点重写
  · 叙事类（角色断线、标志动作过密）—— 只影响后续章节，由约束层管，不回改旧稿

    python3 scripts/repair.py --title 书名 [--dry]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.evaluator import audit                       # noqa: E402
from server.orchestrator import Novelist, Project, call, clean   # noqa: E402

_EN_OK = {"cpu", "dna", "gdp", "app", "kpi", "ceo", "cto"}


def english_hits(t: str):
    return [w for w in re.findall(r"[A-Za-z]{3,}", t) if w.lower() not in _EN_OK]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--terms", action="store_true", help="额外做数字条款一致性修复")
    a = ap.parse_args()

    nv = Novelist(Project(a.title))
    done = sorted(nv.p.state.get("done", []))
    if not done:
        print("没有已写章节")
        return 1
    print(f"《{a.title}》{len(done)} 章，引号主流风格：{nv.quote_style() or '未定'}\n")

    n_fmt = n_sem = 0
    for n in done:
        raw = nv.p.chapter(n)
        if not raw:
            continue

        # ① 格式类：确定性修复
        fixed = nv.normalize_body(raw)
        if fixed != raw:
            n_fmt += 1
            what = []
            if re.search(r"^#{1,6}\s+\S", raw, re.M):
                what.append("markdown标题")
            if raw.count(chr(0x300C)) != fixed.count(chr(0x300C)):
                what.append("引号风格")
            print(f"第{n:3d}章 格式修复：{'、'.join(what)}")
            if not a.dry:
                nv.p.write(nv.p.chapter_path(n), fixed)
            raw = fixed

        # ② 语义类：英文残留交给模型定点改
        en = english_hits(raw)
        if not en:
            continue
        n_sem += 1
        print(f"第{n:3d}章 英文残留：{en}")
        if a.dry:
            continue
        p = (f"下面这章正文里混进了英文单词：{'、'.join(dict.fromkeys(en))}。\n"
             f"请把它们换成符合本书语境的中文（这是一部北宋背景的小说，"
             f"人物对话里不该出现英文），**其余一字不改**，"
             f"不要重写、不要调整段落、不要改标点。\n"
             f"直接输出修改后的完整正文，无前言。\n\n{raw}")
        t2 = clean(call("polishing", p, max_tokens=8192).text)
        t2 = nv.normalize_body(t2)
        if not t2 or english_hits(t2):
            print(f"        改后仍有英文，跳过")
            continue
        # 长度守卫：这一步只该换几个词，字数不该有大变化
        c1 = len(re.findall(r"[一-鿿]", raw))
        c2 = len(re.findall(r"[一-鿿]", t2))
        if abs(c2 - c1) > c1 * 0.15:
            print(f"        改后字数异常（{c1}→{c2}），跳过")
            continue
        nv.p.write(nv.p.chapter_path(n), t2)
        r = audit(t2, extra_blacklist=nv.hard_blacklist(),
                  target_words=nv.target_words(),
                  check_modern=nv.anachronism_check())
        r["target_words"] = nv.target_words()
        nv.p.write(f"audit/{n:03d}.json", json.dumps(r, ensure_ascii=False, indent=2))
        print(f"        ✓ 已改（{c1}→{c2} 字，得分 {r['score']}）")

    print(f"\n格式修复 {n_fmt} 章，语义修复 {n_sem} 章")
    if a.terms:
        return fix_terms(nv, done, a.dry)
    return 0


def fix_terms(nv, done, dry: bool) -> int:
    """数量约定一致性修复 —— 让模型直接读正文，不做正则预筛。

    正则预筛看着省 token，实则漏掉最容易出错的那一类：「一成干股」「三日为限」
    「分十四个月付清」「五五分账」都不带「数字+贯/两」的形状，而这些恰恰是
    前后最容易打架的约定。规则匹配只认它设计者想得到的形状，想不到的就漏。

    改为按窗口把正文原文喂给模型，让它自己认哪些是「被当成规则遵守的数量」，
    再自己判断哪些对不上。窗口按上下文预算切，跨窗口的冲突交给最后一轮汇总。
    """
    import re as _re
    win, cur, cur_len = [], [], 0
    # 单窗口 25k 字符 ≈ 19k tok。60k 会写超时（网关对单次请求体有耐心上限），
    # 而且窗口越大模型越容易漏看中间段落。宁可多切几窗再汇总。
    budget = 25000
    for n in done:
        t = nv.p.chapter(n)
        if not t:
            continue
        piece = f"\n\n========== 第{n}章 ==========\n{nv.condense(t, 3500)}"
        if cur and cur_len + len(piece) > budget:
            win.append(cur)
            cur, cur_len = [], 0
        cur.append(piece)
        cur_len += len(piece)
    if cur:
        win.append(cur)
    print(f"\n正文分 {len(win)} 个窗口交给模型通读…")

    ASK = (
        "你在给一部长篇小说做**数量一致性校对**。下面是正文原文。\n\n"
        "请自己找出文中所有**被当成规则来遵守的数量约定**，包括但不限于：\n"
        "金额与底价、报价、分成比例（一成干股/五五分账/对半）、期限（三日为限/"
        "分十四个月付清）、利率（三分利/月息）、数目（多少人、多少船、多少石）、"
        "折算关系（一贯=多少文、一两银=多少贯）。**不要只看带数字的句子** —— "
        "「对半分」「翻了一倍」「抽一成」这类也算。\n\n"
        "然后判断哪些地方**对不上**：\n"
        "① 同一件事在不同章节数值不同，且正文**没有交代原因**\n"
        "② 算术错误（利息、总价、折算算不通）\n"
        "③ 违反已定的规则（如低于底价却没被判废标）\n\n"
        "⚠️ 数字变了不一定是错：正文若交代了改标、抬价、重议、毁约、折价、"
        "贬值，那是剧情推进，**不要报**。\n\n"
        "只输出 JSON，不要代码围栏，不要在 JSON 之后追加说明：\n"
        '{"conflicts":[{"item":"某笔借契的日息","canonical":"一日五钱",'
        '"why":"立契写明三分利，五百贯按三分利日息就是五钱；后文写成一两五钱，'
        '多算三倍，全书无改息交代","fix":[{"ch":16,"from":"一日一两五钱",'
        '"to":"一日五钱"}]}]}\n'
        "没有冲突就输出 {\"conflicts\":[]}。\n\n")

    def ask(payload: str):
        r = call("judging", ASK + payload, max_tokens=3000)
        raw = _re.sub(r"^```[a-z]*\s*|\s*```$", "", clean(r.text).strip(), flags=_re.M)
        for m in _re.finditer(r"\{", raw):     # 括号配平逐个试解析
            depth, end = 0, None
            for i in range(m.start(), len(raw)):
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if not end:
                continue
            try:
                cand = json.loads(raw[m.start():end])
            except Exception:
                continue
            if isinstance(cand, dict) and "conflicts" in cand:
                return cand.get("conflicts") or []
        print(f"  解析不出 JSON，模型原话：{raw[:200]}")
        return []

    conf = []
    for i, w in enumerate(win, 1):
        got = ask("".join(w))
        print(f"  窗口 {i}/{len(win)}（{len('' .join(w)):,} 字）→ {len(got)} 条")
        conf += got
    if len(win) > 1 and conf:            # 跨窗口的冲突再汇总一轮
        merged = ask("下面是分窗口初判的结果，请去重并剔除误报（尤其是被剧情交代过的变化），"
                     "输出最终 JSON：\n" + json.dumps({"conflicts": conf}, ensure_ascii=False))
        if merged:
            conf = merged

    if not conf:
        print("未发现数量冲突")
        return 0
    for c in conf:
        print(f"\n【{c.get('item')}】权威值：{c.get('canonical')}")
        print(f"  理由：{str(c.get('why', ''))[:100]}")
        for f in c.get("fix", []):
            print(f"  第{f.get('ch')}章：{f.get('from')} → {f.get('to')}")
    if dry:
        return 0

    todo = {}
    for c in conf:
        for f in c.get("fix", []):
            try:
                todo.setdefault(int(f["ch"]), []).append(
                    f"{c['item']}应为「{c['canonical']}」：{f.get('from')} → {f.get('to')}")
            except (KeyError, ValueError, TypeError):
                continue
    for n, items in sorted(todo.items()):
        raw = nv.p.chapter(n)
        if not raw:
            continue
        prompt = (f"下面这一章里的数量与全书其他章节对不上，请改正：\n"
                  + "\n".join(f"- {x}" for x in items) +
                  "\n\n要求：\n"
                  "- 只改这些数量，以及**因为它变了而说不通的那几句**\n"
                  "- 其余一字不改：不重写、不调段落、不改标点、不加情节\n"
                  "- 改完自己核一遍：这一章里所有数量之间要算得通\n"
                  f"直接输出修改后的完整正文，无前言。\n\n{raw}")
        t2 = nv.normalize_body(clean(call("polishing", prompt, max_tokens=8192).text))
        c1 = len(_re.findall(r"[一-鿿]", raw))
        c2 = len(_re.findall(r"[一-鿿]", t2))
        if not t2 or abs(c2 - c1) > c1 * 0.2:
            print(f"  第{n}章改后字数异常（{c1}→{c2}），跳过")
            continue
        nv.p.write(nv.p.chapter_path(n), t2)
        print(f"  ✓ 第{n}章已改（{c1}→{c2} 字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""思考档位 A/B —— 同一模型、同一提示词，只变思考参数，比耗时/思考成本/文体质量。

今天在这件事上连栽三次（并发污染测速、拿 300s 当生产超时、看见快 3 倍就去关思考），
所以做成脚本：变量锁死、产出存盘、直接对标两本原作的真实画像。

    python3 scripts/effort_ab.py --model qwen3.8-max --gw gw4
"""
from __future__ import annotations
import argparse, glob, json, os, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _l in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
from server.prompt_compiler import measure_text          # noqa: E402

KS = ["字数", "对白占比", "每千字问号", "每千字叹号", "段均字数", "独立反问句", "解说体"]
MODES = [("关思考", {"enable_thinking": False}),
         ("low",   {"reasoning_effort": "low"}),
         ("high",  {"reasoning_effort": "high"})]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.8-max")
    ap.add_argument("--gw", default="gw4")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--project", default="", help="用这本书的正文提示词（推荐）")
    a = ap.parse_args()
    gw = a.gw.upper()
    url = os.environ[f"NOVEL_{gw}_URL"].rstrip("/") + "/chat/completions"
    key = os.environ[f"NOVEL_{gw}_KEY"]

    # 必须显式指定提示词来源。早先图省事「自动挑最长的那条」，结果挑到归档书的
    # 提示词 —— 那本用的是旧文风包、旧字数目标、旧结构件，测出来的指标反映的是
    # **那本书的提示词**，跟当前配置毫无关系（字数飙到 6974/8860，原作才 2500~3147）。
    # 比模型/档位时，提示词必须来自同一本书、同一套包。
    if a.prompt:
        P = Path(a.prompt).read_text(encoding="utf-8")
    elif a.project:
        cands = sorted(glob.glob(str(ROOT / "projects" / a.project / "audit/*.prompt.txt")))
        if not cands:
            sys.exit(f"《{a.project}》还没有正文提示词，等它写出第一章再测")
        P = Path(cands[-1]).read_text(encoding="utf-8")
    else:
        sys.exit("必须给 --project 或 --prompt —— 不许自动挑，会挑到别本书的提示词")

    prof = json.loads((ROOT / "packs/profiles/laolatiao.json").read_text(encoding="utf-8"))
    out = ROOT / "reports/effort_ab"; out.mkdir(parents=True, exist_ok=True)
    print(f"提示词 {len(P)} 字 | {a.gw} | {a.model} | 只变思考参数\n", flush=True)
    print(f"{'档位':10s}{'耗时':>7s}{'思考tok':>9s}"
          + "".join(f"{k[:4]:>8s}" for k in KS) + "  达标", flush=True)

    for name, extra in MODES:
        body = {"model": a.model, "messages": [{"role": "user", "content": P}],
                "max_tokens": 16000, "temperature": 0.92}
        body.update(extra)
        t = time.time()
        try:
            r = requests.post(url, json=body, timeout=900,
                              headers={"Authorization": f"Bearer {key}"})
            el = time.time() - t
            if r.status_code != 200:
                print(f"{name:10s}{el:7.0f}s  HTTP {r.status_code} {r.text[:70]}", flush=True)
                continue
            d = r.json(); c = (d["choices"][0]["message"].get("content") or "").strip()
            u = d.get("usage", {})
            rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            if not c:
                print(f"{name:10s}{el:7.0f}s{rt:9d}  输出为空（思考吃光预算）", flush=True)
                continue
            (out / f"{a.model}_{name}.md").write_text(c, encoding="utf-8")
            m = measure_text(c)
            ok = sum(1 for k in KS
                     if any(prof[b][k]["p10"] <= m[k] <= prof[b][k]["p90"] for b in prof))
            print(f"{name:10s}{el:7.0f}s{rt:9d}"
                  + "".join(f"{m[k]:8.2f}" for k in KS) + f"  {ok}/{len(KS)}", flush=True)
        except Exception as e:
            print(f"{name:10s}{time.time()-t:7.0f}s  {type(e).__name__}", flush=True)

    print("\n原作中位：")
    for b, dd in prof.items():
        print(f"{b:10s}{'':16s}" + "".join(f"{dd[k]['中位']:8.2f}" for k in KS), flush=True)
    print(f"\n产出存盘：{out}")


if __name__ == "__main__":
    main()

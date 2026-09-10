#!/usr/bin/env python3
"""模型 A/B —— 同一条真实正文提示词，串行无并发，比速度也比质量。

价格差十倍的时候，「够用就行」比「更好一点」重要得多。所以这里同时给三样：
  · 速度与成功率（长提示词下会不会超时）
  · 文体指标 vs 两本原作的真实画像
  · 产出存盘，可以直接读

    python3 scripts/model_ab.py --models qwen3.8-flash,qwen3.8-max --runs 2
"""
from __future__ import annotations
import argparse, importlib.util, json, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_s = importlib.util.spec_from_file_location("pb", ROOT / "scripts/prompt_bench.py")
pb = importlib.util.module_from_spec(_s); _s.loader.exec_module(pb)
from server.prompt_compiler import measure_text            # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen3.8-flash,qwen3.8-max")
    ap.add_argument("--runs", type=int, default=2)
    # 生产用的是 requests timeout=(60, 600)。测试必须对齐，否则会把
    # 「慢但能用」误判成「不可用」——实测 flash 在 300s 超时，但生产给到 600s。
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--prompt", default="projects/老辣调端到端验证/audit/007.prompt.txt")
    a = ap.parse_args()

    P = (ROOT / a.prompt).read_text(encoding="utf-8")
    out = ROOT / "reports/model_ab"; out.mkdir(parents=True, exist_ok=True)
    prof = json.loads((ROOT / "packs/profiles/laolatiao.json").read_text(encoding="utf-8"))
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    print(f"同一条真实正文提示词 {len(P)} 字；串行无并发；各 {a.runs} 次\n", flush=True)

    rows = []
    for m in models:
        for i in range(1, a.runs + 1):
            t = time.time(); r = pb.call(m, P, 8000, 0.92, retries=1, timeout=a.timeout)
            el = time.time() - t
            ok = not r.startswith("[[调用失败")
            if ok:
                (out / f"{m}_{i}.md").write_text(r, encoding="utf-8")
                mm = measure_text(r); rows.append((m, el, mm))
                print(f"  {m:15s} #{i} {el:5.0f}s {mm['字数']:5d}字 对白{mm['对白占比']:.2f} "
                      f"问{mm['每千字问号']:.1f} 叹{mm['每千字叹号']:.1f} 段均{mm['段均字数']:.0f} "
                      f"独反问{mm['独立反问句']} 解说{mm['解说体']}", flush=True)
            else:
                rows.append((m, None, None))
                print(f"  {m:15s} #{i} {el:5.0f}s  失败 {r[:60]}", flush=True)
            time.sleep(2)

    print("\n════ 速度与成功率 ════")
    for m in models:
        rs = [x for x in rows if x[0] == m]
        good = [x[1] for x in rs if x[1] is not None]
        print(f"  {m:15s} 成功 {len(good)}/{len(rs)}　"
              + (f"中位 {statistics.median(good):.0f}s" if good else "全部失败"))

    print("\n════ 文体指标 vs 原作 ════")
    ks = ["字数", "对白占比", "每千字问号", "每千字叹号", "段均字数", "独立反问句", "解说体"]
    print(f"{'':16s}" + "".join(f"{k[:4]:>8s}" for k in ks))
    for m in models:
        rs = [x[2] for x in rows if x[0] == m and x[2]]
        if not rs:
            print(f"{m:16s} 全部失败"); continue
        print(f"{m:16s}" + "".join(f"{statistics.mean([x[k] for x in rs]):8.2f}" for k in ks))
    for b, d in prof.items():
        print(f"{b:16s}" + "".join(f"{d[k]['中位']:8.2f}" for k in ks))
    print(f"\n产出存盘：{out}")


if __name__ == "__main__":
    main()

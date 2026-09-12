#!/usr/bin/env python3
"""网关自检: 逐个 GET /v1/models, 核对配置里写的模型**确实存在**。

起因: 用户说「模型改用自建的 Qwen3.8-Flash-Next」, 我看到另一个网关上有个
叫 qwen3.8-flash 的, 名字像就当成同一个切了过去 —— 结果在第三方付费实例上
白跑十七个小时, 用户自己的机器一直闲着。
`GET /v1/models` 一秒钟就能问清楚的事, 不该靠名字猜。

用法: python3 scripts/check_gateways.py
"""
import os, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))
from server.registry import registry          # noqa: E402


def main() -> int:
    used = {p["gateway"] for p in registry.profiles.values()}
    bad = 0
    for gid, gw in registry.gateways.items():
        url = (gw.get("base_url") or "").rstrip("/")
        want = gw.get("default_model") or ""
        tag = "★在用" if gid in used else "     "
        if not url:
            print(f"{tag} {gid}: 未配置端点")
            continue
        t = time.time()
        try:
            r = requests.get(url + "/models",
                             headers={"Authorization": f"Bearer {gw.get('api_key') or 'EMPTY'}"},
                             timeout=20)
            if r.status_code != 200:
                print(f"{tag} {gid}: HTTP {r.status_code} —— {r.text[:60]}")
                bad += 1 if gid in used else 0
                continue
            names = [m.get("id") for m in (r.json().get("data") or [])]
            hit = want in names
            print(f"{tag} {gid}: {len(names)} 个模型 / {time.time()-t:.1f}s"
                  f"  配置要的「{want}」{'✓ 在' if hit else '✗ 不在！'}")
            if not hit:
                print(f"        端点实际提供: {'、'.join(str(x) for x in names[:6])}")
                bad += 1
        except Exception as e:
            print(f"{tag} {gid}: {type(e).__name__} {str(e)[:50]}")
            bad += 1 if gid in used else 0
    print(f"\n在用网关: {'、'.join(sorted(used))}")
    if bad:
        print(f"⚠ {bad} 个网关的配置模型对不上 —— 别靠名字猜, 以上面列出的为准")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

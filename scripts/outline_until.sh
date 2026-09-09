#!/usr/bin/env bash
# 排全书细纲的守护 —— 与 run_until.sh 同构。
# cmd_outline 检测到代码变更会退出码 3 交给守护重启；没有守护的话，
# 改一次代码排纲就永久停了（实测排到 79/346 时被我自己的提交打断）。
set -u
cd "$(dirname "$0")/.."
TITLE="${1:?用法: outline_until.sh 书名}"
LOG=reports/outline.log
LOCK=".outline.$(echo -n "$TITLE" | md5sum | cut -c1-8).lock"
PIDFILE=".outline.pid"

# 自建进程组: 后面 cleanup 用 kill -- -$$ 收自己这一组, 不会波及父 shell
set -m 2>/dev/null || true
exec 9>"$LOCK"
flock -n 9 || { echo "已有排纲在跑（$LOCK）"; exit 1; }
echo $$ > "$PIDFILE"
# 退出时**连子进程一起带走**。守护自己被 kill 掉、python 子进程却活着的话,
# 它继承了 fd 9 仍然攥着 flock —— 于是新守护起不来, 报「已有排纲在跑」,
# 要人手 fuser -k 才解得开。踩过两次。
# kill 0 打的是整个进程组, 所以必须先自建进程组, 别误伤调用方。
cleanup() { trap - EXIT INT TERM; rm -f "$PIDFILE"; kill -- -$$ 2>/dev/null; }
trap cleanup EXIT INT TERM

mkdir -p reports
echo "=== 排纲守护启动 $(date '+%F %T')  《$TITLE》 ===" | tee -a "$LOG"
# 连续失败且**一章都没多排出来**，说明是必然失败的错（代码坏了、配置错了），
# 不是网络抖动。实测踩过：一个 TypeError 让守护重试了五十分钟，
# 每次都先花五分钟完整生成一万两千字的细纲、再崩在解析上，全部白烧。
# 重试只该用来扛瞬时错误；扛不动的要停下来喊人。
FAIL_MAX=3
fails=0
prev_n=-1
count_ch() {
  python3 - "$TITLE" <<'PY' 2>/dev/null || echo 0
import json, os, sys
p = os.path.join("projects", sys.argv[1], "chapter_outlines.json")
print(len(json.load(open(p, encoding="utf-8"))) if os.path.exists(p) else 0)
PY
}

while true; do
  python3 run_novel.py outline --title "$TITLE" >> "$LOG" 2>&1
  code=$?
  n=$(count_ch)
  case $code in
    0) echo "-- 排纲完成 $(date '+%T')" | tee -a "$LOG"; break ;;
    3) echo "-- 代码已更新，热轮转 $(date '+%T')" | tee -a "$LOG"; fails=0; sleep 2 ;;
    *)
       if [ "$n" -gt "$prev_n" ]; then
         fails=0                      # 有进展就不算连败
       else
         fails=$((fails + 1))
       fi
       if [ "$fails" -ge "$FAIL_MAX" ]; then
         echo "!! 连续 $fails 次失败且细纲一章没增加（停在 $n 章），"\
              "这不是瞬时错误 —— 守护停机，请看上面的报错 $(date '+%T')" | tee -a "$LOG"
         exit 1
       fi
       echo "-- 异常退出（码 $code），细纲 $n 章，第 $fails/$FAIL_MAX 次重试"\
            "$(date '+%T')" | tee -a "$LOG"
       sleep $((10 * fails)) ;;
  esac
  prev_n=$n
done

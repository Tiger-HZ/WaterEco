# -*- coding: utf-8 -*-
"""存量原文校验与重取标记。

扫描 kb/full/<cid>.txt 与 kb.json 内联 content，用 content_guard 判定：
  - 错误页 / 风控页 / 太短 / 标题不匹配 / 非中文  -> 标记 content_fetched=False、
    删除坏文件、写入 content_reject 原因，等待 refill_all 下一轮重取。
用法：
  python verify_content.py            # 执行校验并写回 kb.json
  DRY=1 python verify_content.py      # 只统计不修改
"""
import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import content_guard as G

KB = os.path.join(BASE, "kb", "kb.json")
FULL = os.path.join(BASE, "kb", "full")
DRY = os.environ.get("DRY", "0") == "1"


def main():
    kb = json.load(open(KB, encoding="utf-8"))
    stat = G.scan_and_reset(kb, FULL, dry=DRY)
    if not DRY:
        json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cf = sum(1 for r in kb if r.get("content_fetched"))
    print("[verify_content] 总=%d  合格=%d  仍标记已取=%d(%.1f%%)"
          % (len(kb), stat.get("ok", 0), cf, 100.0 * cf / max(1, len(kb))))
    for k, v in sorted(stat.items(), key=lambda x: -x[1]):
        print("   %-26s %d" % (k, v))


if __name__ == "__main__":
    main()

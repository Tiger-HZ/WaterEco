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


# ——— 原子安全写 JSON（自动注入，勿手改）———
# 背景：直接用 open(path,"w") + json.dump 有两个致命问题：
#   ① open("w") 会**先清空文件**，若写入中抛异常（如遇到孤立 Unicode 代理码位 \ud835），
#      会留下**半截无效 JSON**；下一步读取失败若又兜底为 []，就会把整库写成空数组（两次线上事故的根因）。
#   ② 非原子写，并发/中断都可能损坏文件。
# 本函数：清洗代理码位与控制字符 → 写临时文件 → os.replace 原子替换。原文件要么不变，要么完整。
def _safe_dump(path, obj):
    import json as _j, os as _o, re as _r, tempfile as _t
    _SURR = _r.compile(r"[\ud800-\udfff]")

    def _clean(x):
        if isinstance(x, str):
            return "".join(c for c in _SURR.sub("", x) if c in "\n\t" or ord(c) >= 32)
        if isinstance(x, list):
            return [_clean(i) for i in x]
        if isinstance(x, tuple):
            return [_clean(i) for i in x]
        if isinstance(x, dict):
            return {k: _clean(v) for k, v in x.items()}
        return x

    obj = _clean(obj)
    d = _o.path.dirname(_o.path.abspath(path)) or "."
    fd, tmp = _t.mkstemp(dir=d, suffix=".tmp")
    try:
        with _o.fdopen(fd, "w", encoding="utf-8") as f:
            _j.dump(obj, f, ensure_ascii=False, indent=1)
        _o.replace(tmp, path)
    except Exception:
        try:
            _o.unlink(tmp)
        except Exception:
            pass
        raise
# ——— 注入结束 ———



KB = os.path.join(BASE, "kb", "kb.json")
FULL = os.path.join(BASE, "kb", "full")
DRY = os.environ.get("DRY", "0") == "1"


def main():
    kb = json.load(open(KB, encoding="utf-8"))
    stat = G.scan_and_reset(kb, FULL, dry=DRY)
    if not DRY:
        _safe_dump(KB, kb)
    cf = sum(1 for r in kb if r.get("content_fetched"))
    print("[verify_content] 总=%d  合格=%d  仍标记已取=%d(%.1f%%)"
          % (len(kb), stat.get("ok", 0), cf, 100.0 * cf / max(1, len(kb))))
    for k, v in sorted(stat.items(), key=lambda x: -x[1]):
        print("   %-26s %d" % (k, v))


if __name__ == "__main__":
    main()

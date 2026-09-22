# -*- coding: utf-8 -*-
"""将首期 data/2026-07-17.json 转为知识库记录，写入 kb/inbox.json 待合并。"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge import event_key


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



BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "data", "2026-07-17.json")
OUT = os.path.join(BASE, "kb", "inbox.json")


def main():
    data = json.load(open(SRC, encoding="utf-8"))
    items = data.get("items", [])
    out = []
    for it in items:
        rec = dict(it)
        rec["content"] = it.get("summary", "")
        rec["content_fetched"] = False
        rec["added_at"] = data.get("date", "2026-07-17")
        rec["_ev"] = event_key(it.get("title", ""))
        out.append(rec)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    _safe_dump(OUT, out)
    print("seeded", len(out))


if __name__ == "__main__":
    main()

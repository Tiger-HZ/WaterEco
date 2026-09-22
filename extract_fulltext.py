# -*- coding: utf-8 -*-
"""水生态环境知识库 · 全文抽取（支撑海量条目、确保原文完整）。
把 kb.json 中体积大的长正文(content) 抽到独立文件 kb/full/<cid>.txt，
kb.json / 分片仅保留元数据 + summary（轻量，门户一次性/分页并行加载无压力）。
详情/问答需要时由门户按需懒加载 kb/full/<cid>.txt，确保每条知识呈现的是原文全部，
而非仅摘录前几句。
幂等：已存在且非空的 full 文件不覆盖；kb 记录 content 置空，标记 content_fetched。
用法：python extract_fulltext.py   （update.py 自动调用，位于 enrich_kg 之后、shard 之前）
"""
import json, os
import crawl_common as C


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
KB = os.path.join(BASE, "kb", "kb.json")
FULL_DIR = os.path.join(BASE, "kb", "full")

def make_cid(r):
    # 与爬虫共用同一函数，保证媒体/全文文件命名一致
    return C.make_cid(r)

def load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return []
    return []

def main():
    os.makedirs(FULL_DIR, exist_ok=True)
    kb = load(KB)
    written = skipped = 0
    seen = set()
    for r in kb:
        cid = r.get("cid") or make_cid(r)
        # 保证 cid 唯一
        while cid in seen:
            cid = cid + "x"
        seen.add(cid)
        r["cid"] = cid
        content = r.get("content") or ""
        fpath = os.path.join(FULL_DIR, cid + ".txt")
        if content and len(content.strip()) > 30:
            if not (os.path.exists(fpath) and os.path.getsize(fpath) > 0):
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                written += 1
            r["content_fetched"] = True
        else:
            r["content_fetched"] = bool(os.path.exists(fpath) and os.path.getsize(fpath) > 0)
        # 轻量化：kb.json / 分片不再保存长正文，门户按需懒加载 kb/full/<cid>.txt
        if "content" in r:
            del r["content"]
        # 确保 summary 存在（详情/检索回退）
        if not r.get("summary"):
            r["summary"] = (r.get("title") or "")[:200]
    _safe_dump(KB, kb)
    print("[extract_fulltext] 写全文=%d 跳过(已存在)=%d kb条数=%d full目录文件数=%d" % (
        written, skipped, len(kb), len(os.listdir(FULL_DIR))))

if __name__ == "__main__":
    main()

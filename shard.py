# -*- coding: utf-8 -*-
"""水生态环境知识库 · 分片存储与轻量检索索引（支撑百万级条目）。
把 kb/kb.json 切成定长分片 kb/shards/shard_NNN.json，并生成：
 - kb/meta.json：总量/分片目录/各维度(分类·区域·部门·年份·质量)倒排，门户首屏仅加载它
 - kb/terms.json：关键词 -> 分片ID 倒排索引，检索时只加载命中的少量分片(再扫描)
门户据此"按需加载分片 + 分页"，突破单文件 kb.json 的体积上限，可承载几十万~百万级。
用法：python shard.py   （update.py 在 extract_fulltext 之后自动调用）
"""
import json, os, re, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
SHARD_DIR = os.path.join(BASE, "kb", "shards")
META = os.path.join(BASE, "kb", "meta.json")
TERMS = os.path.join(BASE, "kb", "terms.json")
SHARD_SIZE = int(os.environ.get("SHARD_SIZE", "2000"))

CATS = ["policy", "standard", "basin_eng", "management", "tech", "literature", "intl_region"]

def tok(s):
    out = []
    cjk = re.findall(r"[\u4e00-\u9fa5]", s or "")
    for i in range(len(cjk) - 1):
        out.append(cjk[i] + cjk[i + 1])
    for w in re.findall(r"[a-z0-9]{2,}", (s or "").lower()):
        out.append(w)
    return out

def counters():
    return {"cats": {}, "regions": {}, "depts": {}, "years": {}, "quality": {}}

def bump(d, k):
    d[k] = d.get(k, 0) + 1

def main():
    os.makedirs(SHARD_DIR, exist_ok=True)
    kb = json.load(open(KB, encoding="utf-8"))
    n = len(kb)
    shards = []
    terms_idx = {}      # term -> set(shard_i)
    cats_idx = {}; regions_idx = {}; depts_idx = {}; years_idx = {}; qual_idx = {}
    for i in range(0, n, SHARD_SIZE):
        chunk = kb[i:i + SHARD_SIZE]
        si = i // SHARD_SIZE
        fname = "shard_%03d.json" % si
        json.dump(chunk, open(os.path.join(SHARD_DIR, fname), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=0)
        c = counters()
        for r in chunk:
            cat = r.get("category") or "other"
            reg = r.get("region") or "其他"
            dep = r.get("department") or "其他"
            yr = (r.get("date") or "")[:4]
            q = r.get("quality") or "C"
            bump(c["cats"], cat); bump(c["regions"], reg); bump(c["depts"], dep)
            if yr.isdigit(): bump(c["years"], yr)
            bump(c["quality"], q)
            for dim, key, idx in (("cats", cat, cats_idx), ("regions", reg, regions_idx),
                                  ("depts", dep, depts_idx), ("years", yr, years_idx),
                                  ("quality", q, qual_idx)):
                idx.setdefault(key, [])
                if si not in idx[key]:
                    idx[key].append(si)
            # 检索词：标题+摘要+标签
            blob = (r.get("title", "") + " " + (r.get("summary") or "") + " " + " ".join(r.get("tags") or []))
            seen = set()
            for t in tok(blob):
                if t in seen:
                    continue
                seen.add(t)
                terms_idx.setdefault(t, set()).add(si)
        shards.append({"i": si, "file": fname, "count": len(chunk),
                       "cats": c["cats"], "regions": c["regions"], "depts": c["depts"],
                       "years": c["years"], "quality": c["quality"]})
    # 字典化倒排
    terms_out = {t: sorted(s) for t, s in terms_idx.items()}
    meta = {
        "total": n, "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "shard_size": SHARD_SIZE, "shard_count": len(shards),
        "shards": shards,
        "cats_index": cats_idx, "regions_index": regions_idx, "depts_index": depts_idx,
        "years_index": years_idx, "quality_index": qual_idx,
        "cats": CATS,
    }
    json.dump(meta, open(META, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(terms_out, open(TERMS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    # 清理多余分片（条目减少时）
    cur = {s["file"] for s in shards}
    for f in os.listdir(SHARD_DIR):
        if f not in cur:
            os.remove(os.path.join(SHARD_DIR, f))
    print("[shard] 总分片=%d 每片=%d 总条目=%d 检索词=%d meta=%d字节 terms=%d字节"
          % (len(shards), SHARD_SIZE, n, len(terms_out),
             os.path.getsize(META), os.path.getsize(TERMS)))

if __name__ == "__main__":
    main()

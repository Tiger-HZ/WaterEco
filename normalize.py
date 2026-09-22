# -*- coding: utf-8 -*-
"""水生态环境知识库 · 字段归一化。
统一 region / department 受控词表，保证门户筛选、今日情报分桶、知识图谱聚合一致。
- region: '全国(部委)' -> '全国'；'其他省市' -> '其他'
- department: 不在受控词表内的 -> '其他'
- URL: 修复 DOI 双重前缀（'https://doi.org/https://doi.org/10.x' -> 'https://doi.org/10.x'）

【2026-09-18 新增】DOI 链接修复：
  排查发现全库 2308 条 doi.org 条目**全部**是双重前缀（crawl_academic 把 OpenAlex 已带
  'https://doi.org/' 的 doi 字段又拼了一次前缀），导致 Crossref 查询全部 404、原文抓不到。
  这里做幂等修复，保证每次 update.py 都会把历史脏数据纠正回来。

对 kb/kb.json 与 kb/inbox.json 同时生效，幂等可重复执行。
用法：python normalize.py
"""
import json, os, re


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
INBOX = os.path.join(BASE, "kb", "inbox.json")

DEPTS = ["生态环境", "水利", "住建", "发改", "农业农村", "自然资源", "市场监管", "其他"]
REGION_MAP = {
    "全国(部委)": "全国",
    "部委": "全国",
    "其他省市": "其他",
}

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)+", re.I)


def norm_region(r):
    reg = r.get("region")
    if reg in REGION_MAP:
        r["region"] = REGION_MAP[reg]
    return r


def norm_dept(r):
    d = r.get("department")
    if d not in DEPTS:
        r["department"] = "其他"
    return r


def norm_url_doi(r):
    """修复 DOI 双重/多重前缀。返回是否发生修复。"""
    u = r.get("url") or ""
    if "doi.org" not in u.lower():
        return False
    fixed = re.sub(r"(?:https?://(?:dx\.)?doi\.org/)+", "https://doi.org/", u, count=1, flags=re.I)
    if fixed != u:
        r["url"] = fixed
        return True
    return False


def load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return []
    return []


def save(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    _safe_dump(p, data)


def process(data, label):
    n_reg = n_dept = n_url = 0
    for r in data:
        before = r.get("region")
        norm_region(r)
        if r.get("region") != before:
            n_reg += 1
        before = r.get("department")
        norm_dept(r)
        if r.get("department") != before:
            n_dept += 1
        if norm_url_doi(r):
            n_url += 1
    print("[normalize %s] region修正=%d department修正=%d DOI链接修正=%d 总=%d"
          % (label, n_reg, n_dept, n_url, len(data)))
    return data


if __name__ == "__main__":
    kb = process(load(KB), "kb")
    save(KB, kb)
    inbox = process(load(INBOX), "inbox")
    save(INBOX, inbox)
    print("normalize done")

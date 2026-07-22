# -*- coding: utf-8 -*-
"""水生态环境知识库 · 字段归一化。
统一 region / department 受控词表，保证门户筛选、今日情报分桶、知识图谱聚合一致。
- region: '全国(部委)' -> '全国'；'其他省市' -> '其他'
- department: 不在受控词表内的 -> '其他'
对 kb/kb.json 与 kb/inbox.json 同时生效，幂等可重复执行。
用法：python normalize.py
"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
INBOX = os.path.join(BASE, "kb", "inbox.json")

DEPTS = ["生态环境", "水利", "住建", "发改", "农业农村", "自然资源", "市场监管", "其他"]
REGION_MAP = {
    "全国(部委)": "全国",
    "部委": "全国",
    "其他省市": "其他",
}

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

def load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return []
    return []

def save(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def process(data, label):
    n_reg = n_dept = 0
    for r in data:
        before = r.get("region")
        norm_region(r)
        if r.get("region") != before:
            n_reg += 1
        before = r.get("department")
        norm_dept(r)
        if r.get("department") != before:
            n_dept += 1
    print("[normalize %s] region修正=%d department修正=%d 总=%d" % (label, n_reg, n_dept, len(data)))
    return data

if __name__ == "__main__":
    kb = process(load(KB), "kb")
    save(KB, kb)
    inbox = process(load(INBOX), "inbox")
    save(INBOX, inbox)
    print("normalize done")

# -*- coding: utf-8 -*-
"""
学术文献源头准入过滤器（供 crawl_academic / crawl_crossref / crawl_europepmc / crawl_pubmed 复用）

作用：在**采集阶段**就拒掉与职责无关学科期刊的论文，避免其进入 kb / inbox
（事后清理成本高，源头过滤最经济）。

政策依据（journal_tier.json）：
  · top（领域顶刊）/ core_cn（国内核心）  -> 直接放行
  · reject_domains（体育/食品/医学/材料/教育/经济/心理等） -> 直接拒绝
  · 其他期刊 -> 需主题强相关（标题含强水生态词，或摘要含 ≥2 个）才放行，标记 tier=ok

用法：
  from academic_filter import journal_ok, journal_name_of
  ok, tier, why = journal_ok(jname, title, abstract)
"""
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))

_TIER = {}
try:
    with open(os.path.join(BASE, "journal_tier.json"), encoding="utf-8") as f:
        _TIER = json.load(f)
except Exception:
    _TIER = {}

REJECT_PATTERNS = [p for p in _TIER.get("reject_domains", {}).get("patterns", []) if p]
TOP_JOURNALS = _TIER.get("tiers", {}).get("top", {}).get("journals", [])
CORE_CN = _TIER.get("tiers", {}).get("core_cn", {}).get("journals", [])

# 强水生态词（源头判定的下限门槛，保守：宁可漏收不可滥收）
STRONG_WATER = [
    "water quality", "water environment", "water pollution", "wastewater", "waste water",
    "sewage", "effluent", "watershed", "river basin", "catchment", "aquatic", "limnology",
    "eutrophication", "nutrient", "nitrogen", "phosphorus", "groundwater", "drinking water",
    "source water", "river", "lake", "reservoir", "stream", "wetland", "estuary", "coastal water",
    "sediment", "macroinvertebrate", "benthic", "phytoplankton", "algae", "cyanobacteria",
    "water treatment", "water reuse", "reclaimed water", "stormwater", "sewer", "runoff",
    "nonpoint source", "water resource", "hydrology", "ecological flow", "river health",
    "水生态", "水环境", "水质", "水污染", "污水处理", "饮用水", "水源", "河湖", "流域", "水体",
    "排污口", "黑臭", "断面", "蓝藻", "富营养化", "再生水", "总氮", "总磷", "地下水", "底栖",
    "溢流", "面源", "湿地", "生态流量", "河长",
]


def journal_name_of(work):
    """从 OpenAlex / Crossref 记录里尽力取期刊名。"""
    if not isinstance(work, dict):
        return ""
    # OpenAlex
    loc = work.get("primary_location") or {}
    src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
    name = (src.get("display_name") or "") if isinstance(src, dict) else ""
    if not name:
        hv = work.get("host_venue") or {}
        name = (hv.get("display_name") or "") if isinstance(hv, dict) else ""
    # 备用字段
    if not name:
        name = work.get("journal") or work.get("container-title") or ""
    if isinstance(name, list):
        name = name[0] if name else ""
    return str(name).strip()


def _hit(text, keys):
    low = (text or "").lower()
    for k in keys:
        if k and k.lower() in low:
            return k
    return ""


def journal_ok(jname, title="", abstract="", require_relevance=True):
    """返回 (ok: bool, tier: str, why: str)

    tier ∈ top / core_cn / ok / reject
    """
    j = (jname or "").strip()
    # 1) 无关学科直接拒
    bad = _hit(j, REJECT_PATTERNS) if j else ""
    if bad:
        return False, "reject", "期刊属无关学科：%s" % bad

    # 2) 顶刊 / 国内核心直接放行
    hit = _hit(j, TOP_JOURNALS) if j else ""
    if hit:
        return True, "top", "领域顶刊：%s" % hit
    hit = _hit(j, CORE_CN) if j else ""
    if hit:
        return True, "core_cn", "国内核心：%s" % hit

    # 3) 其他：需主题强相关
    if not require_relevance:
        return True, "ok", "未分级期刊（未做相关性判定）"
    t_hit = _hit(title, STRONG_WATER)
    if t_hit:
        return True, "ok", "未分级期刊，但标题命中水生态要素：%s" % t_hit
    a_low = (abstract or "").lower()
    n = sum(1 for k in STRONG_WATER if k in a_low)
    if n >= 2:
        return True, "ok", "未分级期刊，但摘要命中 %d 个水生态要素" % n
    if not j:
        return False, "ok", "无期刊信息且主题相关性不足"
    return False, "ok", "未达中高水平门槛或主题相关性不足：%s" % j[:40]


def stats_line():
    return "[academic_filter] 顶刊 %d / 国内核心 %d / 拒收模式 %d" % (
        len(TOP_JOURNALS), len(CORE_CN), len(REJECT_PATTERNS))

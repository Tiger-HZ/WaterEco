# -*- coding: utf-8 -*-
"""
内容准入判定器（gate）——把《内容采集与准入策略》落成可执行闸门

判定每个条目的内容类型（政策法规标准 / 报告公报 / 案例实践 / 专家机构 / 技术方法 / 学术文献 / 动态资讯），
并按该类别的门槛给出 keep / reject 及理由。

核心政策（对应用户要求）：
  · 政策、法规、标准、报告、案例 —— 优先且不遗漏，门槛最宽
  · 学术文献 —— 仍要采，但按 journal_tier.json 分级：只收中高水平 SCI 与国内核心；
                明显无关学科刊（体育/食品/医学/材料/教育/经济等）一律拒
  · 各地好做法 —— 须有借鉴意义或创新性（借鉴性词 + 水生态语境），排除会议/招标类噪声
  · 学术类整体占比 ≤25%，防止淹没政策与案例

用法：
  python3 gate.py --kb kb/kb.json --report gate_report.json      # 只统计，不改数据
  python3 gate.py --kb kb/kb.json --apply                        # 写回 gate_status/content_class
"""
import argparse
import json
import os
import re
import sys
from collections import Counter


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
FULLDIR = os.path.join(BASE, "kb", "full")


def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


TIER = load_json(os.path.join(BASE, "journal_tier.json"))
POLICY = load_json(os.path.join(BASE, "content_policy.json"))
TAX = load_json(os.path.join(BASE, "taxonomy.json"))

# 业务域 core 词（判定"主题确属水生态环境"）
CORE_TERMS = set()
for tv in TAX.get("topic", {}).get("values", []):
    for k in tv.get("core", []):
        CORE_TERMS.add(k)
    for k in tv.get("core_en", []):
        CORE_TERMS.add(k)

# 强水生态词（标题级判定用；与 crawl_sites 的 STRONG 对齐并补充）
STRONG_WATER = ["水生态", "水环境", "地表水", "饮用水", "水源地", "水源保护", "河湖", "流域", "水体",
                "水质", "排污口", "入河排污口", "黑臭", "断面", "蓝藻", "富营养化", "水华", "美丽河湖",
                "幸福河湖", "五水共治", "河湖长", "河长", "再生水", "污水处理", "水污染物", "水功能区",
                "生态流量", "千岛湖", "钱塘江", "苕溪", "运河", "水安全", "湿地", "缓冲带", "地下水",
                "排水", "海绵城市", "水资源", "水生态修复", "尾水", "农田退水", "水域", "河道", "湖泊",
                "水污染", "碧水", "海洋生态", "近岸海域", "总氮", "溢流", "面源", "畜禽粪污", "底栖",
                "water quality", "water environment", "watershed", "river basin", "aquatic",
                "eutrophication", "wastewater", "water resource", "groundwater", "drinking water",
                "macrobenthos", "macroinvertebrate", "river", "lake", "reservoir", "effluent"]

POLICY_LEVELS = {"法律", "行政法规", "部门规章", "规范性文件", "国家标准", "行业标准",
                 "地方性法规", "省政府规章", "地方政府规章", "地方标准"}
REPORT_WORDS = re.compile(r"(公报|状况报告|工作报告|执法检查报告|月报|年报|评估报告|规划（|规划\(|五年规划|专项规划)")
TECH_LEVELS = {"技术导则"}
BORROW = POLICY.get("borrowable_keywords", [])
CASE_NOISE = re.compile("|".join(POLICY.get("case_noise_patterns", [])) or r"$^")
ACAD_CFG = POLICY.get("academic_quality", {})


def ext_text(rec, n=3000):
    c = rec.get("content") or ""
    if not c and rec.get("cid"):
        p = os.path.join(FULLDIR, str(rec["cid"]) + ".txt")
        try:
            if os.path.exists(p):
                c = open(p, encoding="utf-8", errors="ignore").read(n)
        except Exception:
            c = ""
    return c[:n]


def hay_of(rec):
    return " ".join([rec.get("title") or "", rec.get("summary") or "", ext_text(rec)])


def water_hits(rec, title_only=False):
    hay = rec.get("title") or ""
    if not title_only:
        hay += " " + (rec.get("summary") or "")
    low = hay.lower()
    return sum(1 for k in STRONG_WATER if k.lower() in low)


def core_hits(rec):
    hay = hay_of(rec)
    low = hay.lower()
    n = 0
    for k in CORE_TERMS:
        kk = k.lower()
        if kk in low:
            n += 1
            if n >= 3:
                break
    return n


def is_academic(rec):
    u = (rec.get("url") or "").lower()
    return ("doi.org" in u or "openalex" in u or "europepmc" in u or "crossref" in u
            or rec.get("category") == "literature" or rec.get("content_type") == "研究文献")


def journal_of(rec):
    return (rec.get("source") or "") + " " + (rec.get("journal") or "")


def match_journal(journal, names):
    j = journal.lower()
    for n in names:
        if n and n.lower() in j:
            return n
    return ""


def journal_tier(rec):
    """返回 (tier, 命中的刊名或模式)。tier ∈ top/core_cn/ok/reject"""
    j = journal_of(rec)
    for pat in TIER.get("reject_domains", {}).get("patterns", []):
        if pat and pat.lower() in j.lower():
            return "reject", pat
    hit = match_journal(j, TIER.get("tiers", {}).get("top", {}).get("journals", []))
    if hit:
        return "top", hit
    hit = match_journal(j, TIER.get("tiers", {}).get("core_cn", {}).get("journals", []))
    if hit:
        return "core_cn", hit
    return "ok", ""


def classify(rec):
    lv = rec.get("content_type") or ""
    title = rec.get("title") or ""
    if is_academic(rec):
        return "academic"
    if lv in POLICY_LEVELS:
        return "policy_standard"
    if REPORT_WORDS.search(title) or lv in ("公报报告", "国家规划", "省级规划", "市级规划"):
        return "report_bulletin"
    if lv == "技术导则":
        return "tech_method"
    # 案例：借鉴性词 + 非会议噪声
    hay = title + " " + (rec.get("summary") or "")
    if any(k in hay for k in BORROW) and not CASE_NOISE.search(title):
        return "case_practice"
    if rec.get("category") == "tech":
        return "tech_method"
    if "专家" in hay or "团队" in hay or re.search(r"(研究院|科学院|学会|中心|实验室)", title):
        return "expert_org"
    return "news"


def judge(rec):
    """返回 (keep: bool, cls: str, tier: str, reason: str)"""
    cls = classify(rec)
    wh = water_hits(rec)
    ch = core_hits(rec)

    if cls == "policy_standard":
        if wh >= 1 or ch >= 1:
            return True, cls, "", "政策法规标准（有水相关即收）"
        return False, cls, "", "政策法规但不含水生态环境要素"

    if cls == "report_bulletin":
        rel = rec.get("water_rel")
        rel = float(rel) if isinstance(rel, (int, float)) else (0.8 if wh >= 1 else 0.0)
        if wh >= 1 and (rel >= 0.5 or ch >= 1):
            return True, cls, "", "报告/公报/规划（水相关度达标）"
        return False, cls, "", "报告类但水相关度不足"

    if cls == "case_practice":
        hay = (rec.get("title") or "") + " " + (rec.get("summary") or "")
        if CASE_NOISE.search(rec.get("title") or ""):
            return False, cls, "", "会议/招标/人事类噪声，非实践案例"
        if any(k in hay for k in BORROW) and wh >= 1:
            return True, cls, "", "实践案例（有借鉴性表述 + 水生态语境）"
        return False, cls, "", "案例类但缺乏借鉴性表述或非水生态主题"

    if cls == "tech_method":
        rel = rec.get("water_rel")
        rel = float(rel) if isinstance(rel, (int, float)) else (0.8 if wh >= 1 else 0.0)
        if rec.get("content_type") == "技术导则" or wh >= 1 or ch >= 1:
            return True, cls, "", "技术方法与工程"
        return False, cls, "", "技术类但水相关度不足"

    if cls == "expert_org":
        if wh >= 1 or ch >= 1:
            return True, cls, "", "专家/机构/团队信息"
        return False, cls, "", "机构类但非水生态领域"

    if cls == "academic":
        tier, hit = journal_tier(rec)
        if tier == "reject":
            return False, cls, "reject", "期刊属无关学科（%s）" % hit
        # DOI / 摘要长度要求
        u = (rec.get("url") or "")
        has_doi = "doi.org" in u.lower()
        ab_len = len((rec.get("summary") or "")) + len(ext_text(rec, 1200))
        if tier in ("top", "core_cn"):
            if has_doi or hit or ab_len >= ACAD_CFG.get("min_abstract_chars", 150):
                return True, cls, tier, "学术文献（%s：%s）" % (tier, hit or "领域刊")
            return False, cls, tier, "学术文献但缺少 DOI 且摘要过短"
        # ok：需强相关
        rel = rec.get("water_rel")
        rel = float(rel) if isinstance(rel, (int, float)) else 0.0
        need = ACAD_CFG.get("min_water_rel_unknown_journal", 0.7)
        if rel >= need and ch >= 1:
            return True, cls, "ok", "学术文献（一般期刊但主题强相关）"
        if wh >= 2 and ch >= 1:
            return True, cls, "ok", "学术文献（标题含水生态要素且命中业务域）"
        return False, cls, "ok", "学术文献未达中高水平门槛或主题相关性不足"

    # news
    rel = rec.get("water_rel")
    rel = float(rel) if isinstance(rel, (int, float)) else (0.8 if wh >= 2 else 0.0)
    if rel >= 0.7 or wh >= 2:
        return True, cls, "", "动态资讯（水相关度达标）"
    return False, cls, "", "资讯类水相关度不足"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(BASE, "kb", "kb.json"))
    ap.add_argument("--apply", action="store_true", help="写回 gate_status/content_class")
    ap.add_argument("--report", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    kb = load_json(a.kb, [])
    # ——— 读取失败保护：宁可中止，绝不把库写空 ———
    import os as _os_g
    if not kb and _os_g.path.exists(a.kb) and _os_g.path.getsize(a.kb) > 500:
        raise SystemExit("!! kb.json 存在但解析失败（可能被写坏），拒绝继续以免覆盖数据：%s" % a.kb)
    if a.limit:
        kb = kb[:a.limit]

    stat_cls = Counter()
    stat_keep = Counter()
    rej_reason = Counter()
    rejected = []
    acad_kept = Counter()

    for r in kb:
        keep, cls, tier, reason = judge(r)
        stat_cls[cls] += 1
        stat_keep[(cls, "keep" if keep else "reject")] += 1
        if keep and cls == "academic":
            acad_kept[tier or "ok"] += 1
        if not keep:
            rej_reason[reason.split("（")[0]] += 1
            if len(rejected) < 60:
                rejected.append({"title": (r.get("title") or "")[:80], "class": cls, "tier": tier,
                                 "reason": reason, "source": (r.get("source") or "")[:40],
                                 "url": (r.get("url") or "")[:90]})
        if a.apply:
            r["content_class"] = cls
            r["gate_status"] = "keep" if keep else "reject"
            r["gate_reason"] = reason
            if tier:
                r["journal_tier"] = tier

    total = len(kb)
    kept = sum(v for (c, s), v in stat_keep.items() if s == "keep")
    print("[gate] 共 %d 条，通过 %d（%.1f%%），拒收 %d" % (total, kept, 100.0 * kept / max(1, total), total - kept))
    print("\n[gate] 内容类型分布：")
    order = POLICY.get("display_priority", {}).get("order", [])
    for c in order + [x for x in stat_cls if x not in order]:
        if c in stat_cls:
            k = stat_keep.get((c, "keep"), 0)
            print("   %-18s 合计 %5d  通过 %5d  拒 %5d" % (c, stat_cls[c], k, stat_cls[c] - k))
    print("\n[gate] 学术文献通过分级：", dict(acad_kept) or "无")
    print("\n[gate] 拒收原因 Top 12：")
    for k, v in rej_reason.most_common(12):
        print("   %-46s %5d" % (k[:46], v))
    print("\n[gate] 学术类占比：%.1f%%（策略要求 ≤25%%）"
          % (100.0 * stat_cls.get("academic", 0) / max(1, total)))

    if a.report:
        json.dump({"total": total, "kept": kept,
                   "by_class": {c: {"total": stat_cls[c], "keep": stat_keep.get((c, "keep"), 0)}
                                for c in stat_cls},
                   "academic_tier_kept": dict(acad_kept),
                   "reject_reasons": dict(rej_reason),
                   "rejected_sample": rejected},
                  open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n[gate] 报告 -> %s" % a.report)

    if a.apply:
        _safe_dump(a.kb, kb)
        print("[gate] 已写回 %s" % a.kb)


if __name__ == "__main__":
    main()

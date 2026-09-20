# -*- coding: utf-8 -*-
"""
水生态环境知识库 · 自动标注与元数据回填引擎

职责（对应《分类与元数据规范》）：
  1. 业务域 topic 标注（多值，按关键词权重打分）
  2. 文号 doc_no 抽取（正则，绝不编造，抽不到留空）
  3. 文件层级 content_type 判定（标题形态 + 发文机关）
  4. 归口部门 department 归一（受控词表）
  5. 地域 region 校正（按域名 + 关键词）
  6. 重点水体 waterbody 识别
  7. 时效 status 推断（修订/废止词）
  8. importance / quality 重算（分级基准 + 地域加权）

设计原则：
  - 幂等：可反复运行，结果稳定
  - 保守：证据不足则不写（留空优于写错）
  - 可复核：--report 输出每步统计与抽样，便于人工核对
用法：
  python3 annotate.py [--kb kb/kb.json] [--dry] [--report annotate_report.json] [--limit N]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
TAXO = os.path.join(BASE, "taxonomy.json")
FULLDIR = os.path.join(BASE, "kb", "full")   # 外置正文目录 kb/full/<cid>.txt

_ext_cache = {}


def load_ext_text(cid, n=9000):
    """读取外置正文（kb/full/<cid>.txt 前 n 字）。

    重要：长正文在 kb.json 中是**外置**的（仅保留 cid 指针），
    若不读这里，标注只能靠标题+摘要，覆盖率会严重偏低。
    """
    if not cid:
        return ""
    if cid in _ext_cache:
        return _ext_cache[cid]
    txt = ""
    p = os.path.join(FULLDIR, str(cid) + ".txt")
    try:
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="ignore") as f:
                txt = f.read(n)
    except Exception:
        txt = ""
    _ext_cache[cid] = txt
    return txt

# 英文学科词（来自 OpenAlex 学科分类），不是知识图谱实体，直接剔除
EN_NOISE = re.compile(
    r"^(Environmental science|Biology|Chemistry|Ecology|Computer science|Business|Medicine|"
    r"Geography|Engineering|Physics|Mathematics|Materials science|Geology|Agricultural|"
    r"Biochemistry|Genetics|Sociology|Economics|Political science|Psychology|Science|"
    r"Oceanography|Hydrology|Atmospheric|Public health|Nursing|Art|History|Philosophy|"
    r"Biotechnology|Food science|Chemical engineering|Civil engineering|Energy|"
    r"Operations management|Statistics|Zoology|Botany|Microbiology|Toxicology)$", re.I)
EN_WORD = re.compile(r"^[A-Za-z][A-Za-z\s\-&/]{2,}$")


def load_taxonomy():
    with open(TAXO, encoding="utf-8") as f:
        return json.load(f)


TAX = load_taxonomy()
TOPICS = TAX["topic"]["values"]
TOPIC_BY_ID = {t["id"]: t for t in TOPICS}


def norm_title(t):
    return re.sub(r"\s+", "", (t or ""))


def fields_text(rec, n_content=3000):
    """构造匹配文本：标题权重最高，摘要次之，正文再次。

    正文优先取内联 content；为空时回落到外置 kb/full/<cid>.txt。
    """
    title = rec.get("title") or ""
    summary = rec.get("summary") or ""
    content = rec.get("content") or ""
    if not content:
        content = load_ext_text(rec.get("cid"))
    if isinstance(content, str) and len(content) > n_content:
        content = content[:n_content]
    return title, summary, content


# ---------------- 1) 业务域 ----------------
CORE_W = {"t": 4.0, "s": 1.6, "c": 0.9}        # 核心词权重（中文）
CORE_WE = {"t": 3.2, "s": 1.2, "c": 0.6}       # 核心词权重（英文）
WEAK_W = {"t": 1.4, "s": 0.5, "c": 0.25}       # 辅助词权重（中文）
WEAK_WE = {"t": 1.0, "s": 0.4, "c": 0.2}       # 辅助词权重（英文）
KEEP_SCORE = 2.0        # core 命中后的最低分
STRONG_ONLY = 6.0       # 无 core 命中时，仅靠辅助词入选所需的高分


def _zh(box, kw):
    return kw in box


def _en(box, kw):
    return re.search(r"\b" + re.escape(kw).replace(r"\ ", r"\s+") + r"\b", box, re.I) is not None


def _score_pair(rec, core_zh, core_en, weak_zh, weak_en):
    """返回 (总分, 标题/摘要 core 命中数, 仅正文 core 命中数, 命中词列表)

    正文（尤其长文件）用词宽泛，单个 core 词出现在正文里不足以判定主题，
    因此分别计数：cn_ts（标题或摘要命中，可靠证据）与 cn_b（仅正文命中，需多词佐证）。
    """
    t = norm_title(rec.get("title"))
    s = rec.get("summary") or ""
    c = rec.get("content") or ""
    if not c:
        c = load_ext_text(rec.get("cid"))
    if isinstance(c, str) and len(c) > 3000:
        c = c[:3000]
    tl, sl, cl = t.lower(), s.lower(), c.lower()
    score, cn_ts, cn_b, hits = 0.0, 0, 0, []

    def add(w, kw, counted):
        nonlocal score
        if w:
            score += w
            hits.append(kw)
            return counted + 1
        return counted

    for kw in core_zh:
        w_ts = (CORE_W["t"] if _zh(t, kw) else 0.0) + (CORE_W["s"] if _zh(s, kw) else 0.0)
        w_c = CORE_W["c"] if _zh(c, kw) else 0.0
        if w_ts > 0:
            cn_ts = add(w_ts + w_c, kw, cn_ts)
        elif w_c > 0:
            cn_b = add(w_c, kw, cn_b)
    for kw in core_en:
        w_ts = (CORE_WE["t"] if _en(tl, kw) else 0.0) + (CORE_WE["s"] if _en(sl, kw) else 0.0)
        w_c = CORE_WE["c"] if _en(cl, kw) else 0.0
        if w_ts > 0:
            cn_ts = add(w_ts + w_c, kw, cn_ts)
        elif w_c > 0:
            cn_b = add(w_c, kw, cn_b)
    for kw in weak_zh:
        w = (WEAK_W["t"] if _zh(t, kw) else 0.0) + (WEAK_W["s"] if _zh(s, kw) else 0.0) + (WEAK_W["c"] if _zh(c, kw) else 0.0)
        if w:
            score += w
            hits.append(kw)
    for kw in weak_en:
        w = (WEAK_WE["t"] if _en(tl, kw) else 0.0) + (WEAK_WE["s"] if _en(sl, kw) else 0.0) + (WEAK_WE["c"] if _en(cl, kw) else 0.0)
        if w:
            score += w
            hits.append(kw)
    return round(score, 2), cn_ts, cn_b, hits


def annotate_topics(rec, topn=3):
    """业务域标注。

    判定顺序（专业实践：人工权威 > 自动 > 空）：
      0) taxonomy.override 权威映射表命中 -> 直接采用（旗舰法律/综合文件，人工确认过）；
      1) core 核心词命中 且总分 >= KEEP_SCORE(2.0) -> 判定；
      2) 无 core 命中时，仅当辅助词总分 >= STRONG_ONLY(6.0) 才例外入选；
      3) 按总分降序最多取 topn(3)；仍无 -> 返回空（宁缺毋滥）。
    依据：core 词是高区分度专业术语（如"底栖""总氮""入河排污口"）；
         辅助词含泛词，单独命中不足以判定，避免"制度机制"类泛域误标。
    """
    # ---- 0) 权威映射表 ----
    title_raw = rec.get("title") or ""
    ov = TAX.get("override", {}).get("map", {})
    for k in sorted(ov.keys(), key=len, reverse=True):
        if k and k in title_raw:
            tp = ov[k]
            return tp, {t: 9.9 for t in tp}, {t: "high" for t in tp}

    cand = []
    for tv in TOPICS:
        core_zh = [k for k in tv.get("core", []) if k]
        core_en = [k for k in tv.get("core_en", []) if k]
        cset, ceset = set(core_zh), set(core_en)
        weak_zh = [k for k in tv["keywords"] if k and k not in cset]
        weak_en = [k for k in tv.get("keywords_en", []) if k and k not in ceset]
        sc, cn_ts, cn_b, hits = _score_pair(rec, core_zh, core_en, weak_zh, weak_en)
        if sc <= 0:
            continue
        if cn_ts >= 1 and sc >= KEEP_SCORE:
            cand.append((tv["id"], sc, cn_ts, cn_b, hits))
        elif cn_ts == 0 and cn_b >= 2 and sc >= KEEP_SCORE * 1.5:
            cand.append((tv["id"], sc, cn_ts, cn_b, hits))
        elif cn_ts == 0 and cn_b == 0 and sc >= STRONG_ONLY:
            cand.append((tv["id"], sc, cn_ts, cn_b, hits))

    if not cand:
        return [], {}, {}
    cand.sort(key=lambda x: (-x[1], -x[2]))
    picked = cand[:topn]
    topics = [t for t, _, _, _, _ in picked]
    scores, confs = {}, {}
    for t, sc, cn_ts, cn_b, _ in picked:
        scores[t] = sc
        confs[t] = "high" if (cn_ts >= 1 and sc >= 4.0) or cn_ts >= 2 else "low"
    return topics, scores, confs


# ---------------- 2) 文号 ----------------
def extract_doc_no(rec):
    hay = (rec.get("title") or "") + " " + (rec.get("summary") or "")[:300]
    for pat in TAX["doc_no"]["patterns"]:
        m = re.search(pat, hay)
        if m:
            v = m.group(1).strip()
            v = v.replace("—", "-").replace("–", "-").replace("－", "-")
            v = re.sub(r"\s+", " ", v)
            return v
    return ""


# ---------------- 3) 文件层级 ----------------
LEVEL_RULES = [
    ("法律", [r"主席令第", r"中华人民共和国.*法$", r"^中华人民共和国.{2,12}法$"]),
    ("行政法规", [r"国务院令第", r"^.{0,20}条例$", r"实施条例"]),
    ("国家标准", [r"\bGB\s*/?\s*T?\s*\d+", r"国家标准"]),
    ("行业标准", [r"\bHJ\s*/?\s*T?\s*\d+", r"\bSL\s*/?\s*T?\s*\d+", r"行业标准", r"技术规范 HJ"]),
    ("地方标准", [r"\bDB\s*33", r"浙江省地方标准"]),
    ("地方性法规", [r"人民代表大会常务委员会.*(公告|通过)", r"^浙江省.{2,16}条例$", r"^杭州市.{2,16}条例$"]),
    ("部门规章", [r"部令第", r"生态环境部令", r"管理办法$", r"管理规定$"]),
    ("规范性文件", [r"〔\d{4}〕\d+\s*号", r"实施意见", r"行动方案", r"工作方案", r"通知$"]),
    ("国家规划", [r"^.{0,30}规划（?\d{4}", r"五年规划", r"十四五", r"十五五", r"水生态环境保护规划"]),
    ("公报报告", [r"公报", r"状况报告", r"工作报告", r"执法检查报告", r"月报", r"年报", r"评估报告"]),
    ("技术导则", [r"技术指南", r"技术导则", r"指南（", r"技术规程"]),
]


def annotate_level(rec):
    cur = rec.get("content_type") or ""
    if cur in TAX["level"]["values"]:
        return cur
    title = rec.get("title") or ""
    for lv, pats in LEVEL_RULES:
        for p in pats:
            if re.search(p, title):
                return lv
    # 学术文献兜底
    if "doi.org" in (rec.get("url") or "") or rec.get("category") == "literature":
        return "研究文献"
    return cur or "资讯动态"


# ---------------- 4) 归口部门 ----------------
def annotate_dept(rec):
    hay = " ".join([rec.get("title") or "", rec.get("source") or "", rec.get("issuer") or "",
                    rec.get("url") or ""])
    for dept, kws in TAX["department"]["keywords"].items():
        if any(k in hay for k in kws):
            return dept
    u = rec.get("url") or ""
    if re.search(r"(mee|mee\.gov|hjbh|environment)", u):
        return "生态环境"
    if re.search(r"(mwr\.gov|water\.gov|slt\.|slj\.)", u):
        return "水利"
    if re.search(r"(mohurd|jst\.|jsj\.|cxjw)", u):
        return "住建"
    if re.search(r"(ndrc|fgw)", u):
        return "发改"
    if re.search(r"(moa\.gov|nynct|nyj)", u):
        return "农业农村"
    return rec.get("department") or "其他"


# ---------------- 5) 地域 ----------------
HZ_DOMAIN = re.compile(r"(hangzhou\.gov\.cn|hzrd\.gov\.cn|epb\.hangzhou|sthj.*hangzhou|z\.hangzhou)")
ZJ_DOMAIN = re.compile(r"(zj\.gov\.cn|zjrd\.gov\.cn|sthjt\.zj|jst\.zj|slt\.zj|nynct\.zj|cnemc|dbba\.sacinfo)")
HZ_WORDS = ["杭州市", "杭州", "淳安", "余杭", "萧山", "临平", "富阳", "临安", "桐庐", "建德", "钱塘区", "西湖区", "千岛湖", "西溪"]
ZJ_WORDS = ["浙江省", "浙江", "杭州", "宁波", "温州", "湖州", "嘉兴", "绍兴", "金华", "衢州", "舟山", "台州", "丽水", "钱塘江", "苕溪"]


def annotate_region(rec):
    u = rec.get("url") or ""
    hay = (rec.get("title") or "") + (rec.get("summary") or "")[:200]
    if HZ_DOMAIN.search(u) or any(w in hay for w in HZ_WORDS):
        return "杭州"
    if ZJ_DOMAIN.search(u) or any(w in hay for w in ZJ_WORDS):
        return "浙江"
    if re.search(r"(doi\.org|openalex|europepmc|crossref|zenodo|ncbi|arxiv)", u, re.I):
        return "国际"
    for w in TAX["waterbody"]["values"]:
        if w in hay:
            return "流域"
    return rec.get("region") or "全国"


# ---------------- 6) 水体 ----------------
def annotate_waterbody(rec):
    body = rec.get("content") or load_ext_text(rec.get("cid"), 3000)
    hay = (rec.get("title") or "") + (rec.get("summary") or "")[:300] + body[:1500]
    out = []
    for wb, kws in TAX["waterbody"]["keywords"].items():
        if any(k in hay for k in kws):
            out.append(wb)
    return out


# ---------------- 7) 时效 ----------------
def annotate_status(rec):
    cur = rec.get("status") or ""
    hay = (rec.get("title") or "") + (rec.get("summary") or "")[:200]
    inf = TAX["status"]["infer"]
    for st in ("已废止", "已修订"):
        if any(k in hay for k in inf[st]):
            return st
    if cur in TAX["status"]["values"]:
        return cur
    lv = rec.get("content_type") or ""
    if lv in ("法律", "行政法规", "国家标准", "行业标准", "部门规章", "地方性法规", "地方标准", "省政府规章"):
        return "有效"
    return cur or ""


# ---------------- 8) 重要性 / 质量 ----------------
def compute_importance(rec):
    lv = rec.get("content_type") or ""
    base = TAX["importance_rule"]["base_by_level"].get(lv)
    if base is None:
        base = 5.0 if rec.get("category") == "literature" else 5.5
    reg = rec.get("region") or ""
    if reg == "杭州":
        base += 0.6
    elif reg == "浙江":
        base += 0.4
    elif reg == "流域":
        base += 0.3
    title = rec.get("title") or ""
    if re.search(r"(中共中央|国务院办公厅|中办|国办|主席令|生态环境法典)", title):
        base += 0.3
    if rec.get("water_rel") is not None:
        try:
            base += 0.3 * float(rec["water_rel"])
        except Exception:
            pass
    return round(min(10.0, max(0.0, base)), 2)


def compute_quality(rec):
    lv = rec.get("content_type") or ""
    sig = re.search(r"(主席令|国务院令|部令|〔\d{4}〕\d+号)", rec.get("title") or "")
    if lv in ("法律", "行政法规", "国家标准", "部门规章", "国家规划", "地方性法规", "地方政府规章", "省政府规章") or sig:
        return "A"
    if lv in ("行业标准", "地方标准", "规范性文件", "省级规划", "市级规划", "技术导则", "公报报告"):
        return "B"
    if lv == "研究文献":
        return rec.get("quality") or "C"
    if rec.get("quality") in ("A", "B", "C"):
        return rec["quality"]
    return "C"


# ---------------- 9) kg_terms 清洗 ----------------
def clean_kg(rec):
    t = rec.get("kg_terms")
    if not isinstance(t, list):
        return None
    out = []
    for x in t:
        s = str(x).strip()
        if not s or EN_NOISE.match(s) or EN_WORD.match(s):
            continue
        if re.match(r"^\d{4}$", s):     # 纯年份
            continue
        if len(s) == 1:
            continue
        if s not in out:
            out.append(s)
    return out[:14]


# ---------------- 10) 知识类别 kclass ----------------
_KCLASS = {}


def _kclass_cfg():
    global _KCLASS
    if not _KCLASS:
        try:
            with open(os.path.join(BASE, "kclass.json"), encoding="utf-8") as f:
                _KCLASS = json.load(f)
        except Exception:
            _KCLASS = {}
    return _KCLASS


def annotate_kclass(rec):
    """知识类别（面向用户）：政策法规/标准规范/规划与报告/管理机制/案例实践/技术产品/
    流域治理与工程/科研文献/数据资源/概念术语/教育科普/重要讲话/机构信息/人物专家/新闻媒体/其他

    判定：① 文件层级精确归类（最可靠）→ ② 标题/摘要特征词 → ③ 多命中按 order 取优先级最高者。
    """
    # DOI 链接即学术文献，直接判定（避免英文文献被误归资讯/新闻）
    u = (rec.get("url") or "").lower()
    if "doi.org" in u or "openalex" in u or "europepmc" in u:
        return "research"
    cfg = _kclass_cfg()
    order = cfg.get("order") or [c["id"] for c in cfg.get("classes", [])]
    lv = rec.get("content_type") or ""
    hay = " ".join([rec.get("title") or "", rec.get("summary") or "",
                    (rec.get("content") or "")[:1500]])
    hits = set()
    for c in cfg.get("classes", []):
        if lv and lv in (c.get("by_level") or []):
            hits.add(c["id"])
        for kw in (c.get("keywords") or []):
            if kw and kw in hay:
                hits.add(c["id"])
                break
    for cid in order:
        if cid in hits:
            return cid
    return "other"


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(BASE, "kb", "kb.json"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    kb = json.load(open(a.kb, encoding="utf-8"))
    if a.limit:
        kb = kb[:a.limit]
    stat = Counter()
    topic_cnt = Counter()
    sample = defaultdict(list)
    changed = 0

    for r in kb:
        before = json.dumps({k: r.get(k) for k in
                             ("topic", "topic_conf", "kclass", "doc_no", "content_type", "department", "region", "waterbody", "status", "importance", "quality", "kg_terms")},
                            ensure_ascii=False, sort_keys=True)

        topics, tscores, tconf = annotate_topics(r)
        if topics:
            r["topic"] = topics
            r["topic_score"] = tscores
            r["topic_conf"] = tconf
            for t in topics:
                topic_cnt[TOPIC_BY_ID[t]["zh"]] += 1
        else:
            stat["topic_none"] += 1

        dn = extract_doc_no(r)
        if dn and dn != r.get("doc_no"):
            r["doc_no"] = dn
            stat["doc_no_filled"] += 1

        lv = annotate_level(r)
        if lv and lv != r.get("content_type"):
            r["content_type"] = lv
            stat["level_filled"] += 1

        d = annotate_dept(r)
        if d and d != r.get("department"):
            r["department"] = d
            stat["dept_filled"] += 1

        rg = annotate_region(r)
        if rg and rg != r.get("region"):
            r["region"] = rg
            stat["region_fixed"] += 1

        wb = annotate_waterbody(r)
        if wb:
            r["waterbody"] = wb
            stat["waterbody"] += 1

        st = annotate_status(r)
        if st and st != r.get("status"):
            r["status"] = st

        r["importance"] = compute_importance(r)
        r["quality"] = compute_quality(r)

        r["kclass"] = annotate_kclass(r)

        ck = clean_kg(r)
        if ck is not None and ck != r.get("kg_terms"):
            r["kg_terms"] = ck
            stat["kg_cleaned"] += 1

        after = json.dumps({k: r.get(k) for k in
                            ("topic", "topic_conf", "kclass", "doc_no", "content_type", "department", "region", "waterbody", "status", "importance", "quality", "kg_terms")},
                           ensure_ascii=False, sort_keys=True)
        if before != after:
            changed += 1

        # 抽样（便于人工复核）
        for t in topics[:1]:
            if len(sample[t]) < 3:
                sample[t].append({"title": (r.get("title") or "")[:70], "doc_no": r.get("doc_no"),
                                  "level": r.get("content_type"), "region": r.get("region"),
                                  "score": round(tscores.get(t, 0), 2)})

    print("[annotate] 处理 %d 条，变更 %d 条" % (len(kb), changed))
    for k, v in sorted(stat.items()):
        print("   %-16s %d" % (k, v))
    print("\n[annotate] 业务域分布（Top 25）：")
    for k, v in topic_cnt.most_common(25):
        print("   %-26s %5d" % (k, v))
    lv_cnt = Counter((r.get("content_type") or "(空)") for r in kb)
    print("\n[annotate] 层级分布：")
    for k, v in lv_cnt.most_common(20):
        print("   %-16s %5d" % (k, v))
    print("\n[annotate] 地域分布：", dict(Counter((r.get("region") or "(空)") for r in kb).most_common()))
    print("[annotate] 部门分布：", dict(Counter((r.get("department") or "(空)") for r in kb).most_common(8)))

    if a.report:
        json.dump({"stat": dict(stat), "changed": changed, "total": len(kb),
                   "topic_dist": dict(topic_cnt), "level_dist": dict(lv_cnt),
                   "sample": {k: v for k, v in sample.items()}},
                  open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n[annotate] 报告 -> %s" % a.report)

    if not a.dry and not a.limit:
        json.dump(kb, open(a.kb, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[annotate] 已写回 %s" % a.kb)


if __name__ == "__main__":
    main()

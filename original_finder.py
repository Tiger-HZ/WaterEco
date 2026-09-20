# -*- coding: utf-8 -*-
"""原文检查系统（original_finder）—— 入库前审查 + 入库后巡检。

用户要求（2026-09-20）：
 1) 每条知识**入库前后都要检查**；
 2) 采集时若发现只是"讯息/报道/解读"（例如只发了"生态环境法典颁布"的新闻，
    网站上并没有原文），要**主动去检索原文**（Word/PDF 均可），确保拿到的是原文；
 3) 有多个出处时，要**比对哪个更全、哪个更权威**，并**标注原文来源**；
 4) 入库后若缺原文要补收；补不到就明确标注「暂未收集到原文全文」；
 5) 绝不能拿不对应的别的东西充当原文（对应性由 content_guard 的标题匹配度把关）。

实现：把"原文"当作一次**多源检索 + 打分择优**的问题。
 候选来源：
   A. 国务院政策文件库（sousuo.www.gov.cn）—— 用「被指向的文档名」检索，优先公文/部门文件/公报
   B. 当前页面内指向"原文/附件/全文"的链接（解读页常带原文链接）
   C. 已入库同主题条目的更权威出处（同标题指纹的更高分记录）
 每个候选抓正文/PDF 后打分：
     score = 0.40*权威性 + 0.30*完整度 + 0.30*对应性
 取最高分作为 origin，其余进 origin_alternatives（供人工复核）。

用法：
  python original_finder.py                 # 入库前审查：处理 origin_status 缺失/待审查的条目
  python original_finder.py --audit         # 入库后巡检：校验已有原文 + 标注暂未收录
  LIMIT=200 python original_finder.py       # 限制本次处理条数
"""
import json, os, re, sys, time, difflib
from urllib.parse import urljoin, urlparse, quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdfutil
import content_guard as G
from fetch_fulltext import extract_main_text

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
LIMIT = int(os.environ.get("LIMIT", "120"))
MODE_AUDIT = "--audit" in sys.argv
ONLY = os.environ.get("ONLY", "").strip()
MAIL = os.environ.get("OA_MAIL", "water-eco-bot@users.noreply.github.com")

MIN_LEN = int(os.environ.get("ORIGIN_MIN_LEN", "600"))
MIN_MATCH = float(os.environ.get("ORIGIN_MIN_MATCH", "0.45"))
ACCEPT_SCORE = float(os.environ.get("ORIGIN_ACCEPT", "0.55"))

# ——— 权威性分级（域名 -> 分数、标签）———
AUTH = [
    (r"(^|\.)gov\.cn$|(^|\.)mee\.gov\.cn$|(^|\.)npc\.gov\.cn$|(^|\.)mwr\.gov\.cn$|"
     r"(^|\.)mohurd\.gov\.cn$|(^|\.)ndrc\.gov\.cn$|(^|\.)moa\.gov\.cn$|(^|\.)samr\.gov\.cn$|"
     r"(^|\.)mnr\.gov\.cn$|(^|\.)court\.gov\.cn$",
     1.00, "国家级官方"),
    (r"(^|\.)zj\.gov\.cn$|(^|\.)zjrd\.gov\.cn$|(^|\.)sthjt\.zj\.gov\.cn$",
     0.88, "浙江省级官方"),
    (r"(^|\.)hangzhou\.gov\.cn$|(^|\.)hzrd\.gov\.cn$|(^|\.)epb\.hangzhou\.gov\.cn$",
     0.82, "杭州市级官方"),
    (r"(^|\.)people\.com\.cn$|(^|\.)xinhuanet\.com$|(^|\.)news\.cn$|(^|\.)chinanews\.com\.cn$|"
     r"(^|\.)people\.cn$|(^|\.)gmw\.cn$|(^|\.)qstheory\.cn$",
     0.62, "中央权威媒体"),
    (r"(^|\.)chinacourt\.org$|(^|\.)legaldaily\.com\.cn$|(^|\.)chinacourt\.cn$",
     0.55, "政法专业媒体"),
    (r"(^|\.)doi\.org$|(^|\.)openalex\.org$|(^|\.)springer|(^|\.)sciencedirect|(^|\.)wiley|"
     r"(^|\.)mdpi\.com$|(^|\.)nature\.com$|(^|\.)acs\.org$|(^|\.)rsc\.org$",
     0.70, "学术出版方"),
]
DEFAULT_AUTH = (0.40, "其他来源")

NEWSY = re.compile(r"(一图读懂|图解|解读|答记者问|新闻发布会|微课堂|视频|动漫|访谈|"
                   r"专家观点|评论|综述|快讯|简讯|消息|报道|观察|侧记|盘点|问答)")
TITLE_KEEP = re.compile(r"《([^》]{4,60})》")


def authority(u):
    h = urlparse(u or "").netloc.lower()
    for pat, sc, label in AUTH:
        if re.search(pat, h, re.I):
            return sc, label
    return DEFAULT_AUTH


def is_newsy(rec):
    return bool(NEWSY.search(rec.get("title") or ""))


def doc_query(rec):
    """从条目标题推出"要找的原文文档名"。"""
    t = rec.get("title") or ""
    m = TITLE_KEEP.search(t)
    if m:
        return m.group(1)
    t = NEWSY.sub("", t)
    t = re.sub(r"[（(【\[].*?[)）】\]]", "", t)
    t = re.sub(r"(全文|全文发布|发布|印发|的通知|的通知全文|政策原文|原文)$", "", t)
    t = re.sub(r"^.{0,12}?[：:·｜|]\s*", "", t)
    t = re.sub(r"[\s“”\"'《》]+", "", t)
    return t.strip()[:60]


def match_score(title_a, title_b):
    a = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", title_a or "")
    b = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", title_b or "")
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    # 互相包含给加成
    if a in b or b in a:
        r = max(r, 0.85)
    return r


# ——— 候选源 A：国务院政策文件库 ———
def search_govlib(query):
    """按文档名检索国务院政策文件库；优先公文/部门文件/公报（=原文本身）。"""
    api = ("https://sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary"
           "&q=%s&p=1&n=20&type=gwyzcwjk&sort=&sortType=1&searchfield=" % quote(query))
    html = pdfutil.fetch_html(api, timeout=20)
    if not html:
        return []
    try:
        d = json.loads(html)
    except Exception:
        return []
    cm = (d.get("searchVO") or {}).get("catMap") or {}
    prefer = ["gongwen", "bumenfile", "gongbao", "otherfile"]
    out = []
    for key in prefer:
        for it in ((cm.get(key) or {}).get("listVO") or []):
            u = (it.get("url") or "").strip()
            t = re.sub(r"<[^>]+>", "", it.get("title") or "")
            t = re.sub(r"\s+", " ", t).strip()
            if not u or not t:
                continue
            out.append({
                "url": u, "title": t, "cat": key,
                "source": (it.get("puborg") or "").strip(),
                "doc_no": (it.get("pcode") or "").strip(),
                "date": (it.get("pubtimeStr") or "")[:10],
                "from": "国务院政策文件库",
            })
        if len(out) >= 60:      # 四个类目都收（公文/部门文件/公报/解读），后续按匹配度预排序
            break
    return out


# ——— 候选源 B：当前页面里的"原文/附件/全文"链接 ———
ORIG_LINK = re.compile(r"(原文|全文|附件|下载|政策原文|文件原文|点击查看|查看全文|\.pdf|\.docx?)", re.I)
# gov.cn 政策原文/公报的路径特征（解读页常直接链到原文）
ORIG_PATH = re.compile(r"(/zhengce/zhengceku/|/zhengce/content/|/gongbao/|/zhengce/20\d{2}|"
                       r"/xxgk\d*/|/gkml/|/art/20\d{2}/|/col/col\d+/art/)", re.I)


def from_page_links(rec):
    url = rec.get("url") or ""
    html = pdfutil.fetch_html(url, timeout=18)
    if not html:
        return []
    out = []
    host = urlparse(url).netloc
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        href, txt = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        txt = re.sub(r"\s+", " ", txt).strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        u = urljoin(url, href)
        if u.rstrip("/") == url.rstrip("/"):
            continue
        is_orig = bool(ORIG_LINK.search(href + " " + txt)) or bool(ORIG_PATH.search(u))
        if not is_orig:
            continue
        out.append({"url": u, "title": txt or "(原文/附件)", "cat": "link",
                    "source": host, "doc_no": "", "date": "", "from": "页面内原文链接"})
    return out[:6]


# ——— 抓取候选正文/PDF ———
def hydrate(cand):
    u = cand["url"]
    a_sc, a_label = authority(u)
    cand["authority"] = a_sc
    cand["authority_label"] = a_label
    text, kind, pdf_rel = "", "", None
    if u.lower().endswith((".pdf", ".doc", ".docx")):
        pdfutil.ensure_dir()
        cid = C_SAFE_ID(u)
        dest = os.path.join(pdfutil.PDF_DIR, cid + ".pdf")
        if pdfutil.download_binary(u, dest):
            t = pdfutil.extract_pdf_text(dest)
            if t:
                text, kind, pdf_rel = t, "PDF", "kb/pdf/%s.pdf" % cid
    else:
        html = pdfutil.fetch_html(u, timeout=20)
        if html:
            # 页面内挂 PDF 的，优先取 PDF 原文
            pdfutil.ensure_dir()
            cid = C_SAFE_ID(u)
            for pu in pdfutil.find_pdf_links(html, u)[:2]:
                dest = os.path.join(pdfutil.PDF_DIR, cid + ".pdf")
                if pdfutil.download_binary(pu, dest):
                    t = pdfutil.extract_pdf_text(dest)
                    if len(t) >= 400:
                        text, kind, pdf_rel = t, "PDF", "kb/pdf/%s.pdf" % cid
                        break
            if not text:
                t = extract_main_text(html) or ""
                t = "\n".join(l for l in t.split("\n") if len(l) >= 4)
                if t:
                    text, kind = t, "正文"
    cand["text"] = text
    cand["kind"] = kind
    cand["pdf"] = pdf_rel
    cand["len"] = len(text or "")
    return cand


def C_SAFE_ID(u):
    import hashlib
    return hashlib.sha1(u.strip().lower().encode("utf-8")).hexdigest()[:16]


def norm_key(s):
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", s or "")


def explicit_targets(title):
    """标题里用《》明确点名的文档 —— 这是"必须对得上"的硬约束。"""
    return [norm_key(t) for t in TITLE_KEEP.findall(title or "") if len(norm_key(t)) >= 6]


def score(cand, rec):
    auth = cand.get("authority", 0.4)
    comp = min(1.0, (cand.get("len") or 0) / 4000.0)
    mt = max(match_score(rec.get("title"), cand.get("title")),
             match_score(doc_query(rec), cand.get("title")))
    cand["match"] = round(mt, 3)
    # ① 对应性硬约束：标题用《》点名了文档，候选必须"包含该文档名"或高度相似，
    #    否则一律判废 —— 防止拿"别的文件"充当原文（用户明确要求）。
    tgts = explicit_targets(rec.get("title"))
    ct = norm_key(cand.get("title"))
    if tgts:
        if not any(t in ct or ct in t for t in tgts):
            cand["ok"] = False
            cand["score"] = 0.0
            cand["guard"] = "explicit_title_mismatch"
            return cand
        if mt < 0.80:
            cand["ok"] = False
            cand["score"] = 0.0
            cand["guard"] = "explicit_title_low_similarity:%.2f" % mt
            return cand
    # ② 一般约束：匹配度与长度门槛
    if mt < MIN_MATCH or (cand.get("len") or 0) < MIN_LEN:
        cand["ok"] = False
        cand["score"] = 0.0
        return cand
    ok, reason = G.is_valid(cand.get("text") or "", cand.get("title") or "")
    cand["guard"] = reason
    cand["ok"] = bool(ok)
    s = 0.40 * auth + 0.30 * comp + 0.30 * mt
    if cand.get("kind") == "PDF":
        s += 0.03          # 同等条件下 PDF 版本更接近原文
    cand["score"] = round(s, 3) if ok else 0.0
    return cand


def find_original(rec):
    """多源检索 + 择优。返回 (best, alternatives, trace)。"""
    q = doc_query(rec)
    cands = []
    trace = []
    if q:
        g = search_govlib(q)
        trace.append("国务院政策文件库('%s') 命中 %d" % (q, len(g)))
        cands += g
    p = from_page_links(rec)
    trace.append("页面内原文链接 %d" % len(p))
    cands += p
    # 去重
    seen, uniq = set(), []
    for c in cands:
        k = c["url"].strip().lower().rstrip("/")
        if k in seen:
            continue
        seen.add(k)
        c["pre_match"] = round(max(match_score(rec.get("title"), c.get("title")),
                                   match_score(doc_query(rec), c.get("title"))), 3)
        uniq.append(c)
    if not uniq:
        return None, [], trace
    # 关键：**先按标题匹配度预排序再抓取**，只 hydrate 最像的若干条，
    # 否则会把抓取预算耗在检索结果里的无关条目上（此前 20 命中却 0 通过的原因）。
    uniq.sort(key=lambda c: -(c["pre_match"] + 0.1 * authority(c["url"])[0]))
    pool = [c for c in uniq if c["pre_match"] >= max(0.30, MIN_MATCH - 0.2)][:6] or uniq[:4]
    trace.append("预排序后抓取候选 %d（最高匹配 %.2f）" % (len(pool), pool[0]["pre_match"]))
    scored = []
    for c in pool:
        try:
            hydrate(c)
            scored.append(score(c, rec))
        except Exception as e:
            trace.append("候选抓取失败 %s" % repr(e)[:60])
        time.sleep(0.2)
    scored = [c for c in scored if c.get("ok")]
    if not scored:
        return None, [], trace + ["候选均不合格（对应性/长度/内容校验未过）"]
    scored.sort(key=lambda c: -c["score"])
    return scored[0], scored[1:4], trace


def apply_origin(rec, best, alts, trace):
    rec["origin_checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if not best:
        rec["origin_status"] = "暂未收录"
        rec["origin_note"] = "已检索：" + "；".join(trace) + "。暂未收集到原文全文，待后续补充。"
        return "none"
    rec["origin_status"] = "原文已核"
    rec["origin_url"] = best["url"]
    rec["origin_title"] = best["title"]
    rec["origin_source"] = "%s（%s）" % (best.get("source") or best.get("from") or urlparse(best["url"]).netloc,
                                        best.get("authority_label", "其他来源"))
    rec["origin_domain"] = urlparse(best["url"]).netloc
    rec["origin_kind"] = best.get("kind") or ""
    rec["origin_len"] = best.get("len") or 0
    rec["origin_score"] = best.get("score") or 0
    rec["origin_authority"] = best.get("authority_label") or ""
    rec["origin_from"] = best.get("from") or ""
    rec["origin_note"] = "；".join(trace) + ("；已比对 %d 个候选出处" % (len(alts) + 1))
    rec["origin_alternatives"] = [
        {"url": a["url"], "title": a["title"], "domain": urlparse(a["url"]).netloc,
         "len": a.get("len"), "score": a.get("score"),
         "authority": a.get("authority_label")}
        for a in alts
    ]
    # 原文正文/PDF 直接用于本条知识
    if best.get("text") and (best.get("len") or 0) > (len(rec.get("content") or "")):
        rec["content"] = best["text"]
        rec["content_fetched"] = True
        rec["content_kind"] = "pdf全文" if best.get("kind") == "PDF" else "正文全文"
    if best.get("pdf"):
        rec["pdf"] = best["pdf"]
    return "ok"


def review_pre(rec):
    """入库前审查：本条是否已是原文？不是则去找原文。"""
    if rec.get("origin_status") in ("原文已核",):
        return "skip"
    # 学术条目走 refill_all 的学术通道，这里不重复
    u = rec.get("url") or ""
    if "doi.org" in u or "openalex.org" in u:
        return "skip"
    cur = rec.get("content") or ""
    if cur and len(cur) >= MIN_LEN:
        ok, reason = G.is_valid(cur, rec.get("title") or "")
        if ok and not is_newsy(rec):
            rec["origin_status"] = "原文已核"
            rec["origin_url"] = u
            rec["origin_title"] = rec.get("title") or ""
            a, lab = authority(u)
            rec["origin_source"] = "%s（%s）" % (urlparse(u).netloc, lab)
            rec["origin_kind"] = "正文" if not rec.get("pdf") else "PDF"
            rec["origin_len"] = len(cur)
            rec["origin_score"] = round(0.40 * a + 0.30 * min(1, len(cur) / 4000.0) + 0.30 * 1.0, 3)
            rec["origin_authority"] = lab
            rec["origin_note"] = "本条即原文（内容校验通过）"
            return "self"
    best, alts, trace = find_original(rec)
    return apply_origin(rec, best, alts, trace)


def main():
    kb = json.load(open(KB, encoding="utf-8"))
    if MODE_AUDIT:
        todo = [r for r in kb if r.get("origin_status") != "原文已核"]
    else:
        todo = [r for r in kb if r.get("origin_status") in (None, "", "待审查")]
    if ONLY:
        todo = [r for r in todo if ONLY in (r.get("title") or "")]
    todo.sort(key=lambda r: (int(r.get("must_rank") or 999), -(float(r.get("importance") or 0))))
    print("待审查 %d 条，本次上限 %d（模式：%s）" % (len(todo), LIMIT, "巡检" if MODE_AUDIT else "入库前审查"))

    stat = {"self": 0, "ok": 0, "none": 0, "skip": 0, "err": 0}
    done = 0
    for r in todo:
        if done >= LIMIT:
            break
        try:
            res = review_pre(r) if not MODE_AUDIT else _audit_one(r)
            stat[res] = stat.get(res, 0) + 1
        except Exception as e:
            stat["err"] += 1
            r["origin_note"] = "审查异常：%s" % repr(e)[:80]
        done += 1
        if done % 10 == 0:
            print("  ..%d/%d  %s" % (done, min(LIMIT, len(todo)), stat), flush=True)
            json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(0.15)
    json.dump(kb, open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = sum(1 for r in kb if r.get("origin_status") == "原文已核")
    none = sum(1 for r in kb if r.get("origin_status") == "暂未收录")
    pending = sum(1 for r in kb if r.get("origin_status") in (None, "", "待审查"))
    print("=" * 56)
    print("original_finder: %s" % stat)
    print("全库原文状态：已核 %d ｜ 暂未收录 %d ｜ 待审查 %d ｜ 合计 %d" % (ok, none, pending, len(kb)))


def _audit_one(r):
    """入库后巡检：内容校验 + 缺原文则补收，补不到标注暂未收录。"""
    cid = r.get("cid") or ""
    fpath = os.path.join(BASE, "kb", "full", cid + ".txt") if cid else ""
    text = r.get("content") or ""
    if not text and fpath and os.path.exists(fpath):
        try:
            text = open(fpath, encoding="utf-8", errors="replace").read()
        except Exception:
            text = ""
    if text:
        okc, reason = G.is_valid(text, r.get("title") or "")
        if okc:
            r["origin_status"] = "原文已核"
            r.setdefault("origin_url", r.get("url"))
            if not r.get("origin_source"):
                a, lab = authority(r.get("url") or "")
                r["origin_source"] = "%s（%s）" % (urlparse(r.get("url") or "").netloc, lab)
                r["origin_kind"] = "PDF" if r.get("pdf") else "正文"
                r["origin_len"] = len(text)
                r["origin_authority"] = lab
            r.pop("content_reject", None)
            return "self"
        r["content_reject"] = reason
    u = r.get("url") or ""
    if "doi.org" in u or "openalex.org" in u:
        r["origin_status"] = "暂未收录"
        r["origin_note"] = "学术文献：暂未收集到可获取的原文全文（已尝试 Unpaywall/Crossref/OpenAlex/DataCite）"
        return "none"
    best, alts, trace = find_original(r)
    return apply_origin(r, best, alts, trace)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""仓库体积治理（prune_repo）。

背景（2026-09-20 审计）：
  仓库总 1.21 GB，其中 kb/pdf 占 1.15 GB（960 个 PDF，中位 461KB，最大 23.6MB，
  41 个 >5MB 合计 351MB）。**GitHub Pages 站点上限 1 GB** —— 已经踩线，
  这正是 run 35448708687「上传站点产物」失败的原因，若不治理会再次导致站点停更。

策略（"原文优先、体积可控"）：
  1) 分级保留 PDF：
     A 级保留 —— 国内政策/法规/标准/管理类（region ∈ 全国/浙江/杭州/长三角，
                 category ∈ policy/standard/management）且体积 ≤ KEEP_MAX_MB(默认 12MB)
     B 级抽文本后删 —— 其余 PDF（学术论文、国际资讯）或超大文件：
                 先把文字抽到 kb/full/<cid>.txt，抽成功即删除 PDF，
                 记录 pdf_pruned=1 + 说明，门户改为"原文见来源链接"。
                 抽不出文字的（扫描件）不删，避免丢掉唯一原文。
  2) 清理仓库根目录历史快照 push-*.html / archive.html（index.html 未引用，共约 18MB）。
  3) 总预算控制：若处理后 kb/pdf 仍超 BUDGET_MB(默认 700MB)，
     按「非 A 级优先、体积从大到小」继续裁剪，直到达标。

用法：
  python prune_repo.py              # 治理并落盘
  DRY=1 python prune_repo.py        # 只报告不删除
  BUDGET_MB=600 KEEP_MAX_MB=8 python prune_repo.py
"""
import os, re, json, glob, time


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
PDF_DIR = os.path.join(BASE, "kb", "pdf")
FULL_DIR = os.path.join(BASE, "kb", "full")

DRY = os.environ.get("DRY", "0") == "1"
KEEP_MAX_MB = float(os.environ.get("KEEP_MAX_MB", "12"))
BUDGET_MB = float(os.environ.get("BUDGET_MB", "700"))

CORE_REGIONS = {"全国", "浙江", "杭州", "长三角"}
CORE_CATS = {"policy", "standard", "management"}
PAGE_EXT = re.compile(r"^push-.*\.html$|^archive\.html$")


def mb(n):
    return round(n / 1048576.0, 2)


def extract_text(path):
    try:
        import sys
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import pdfutil
        return pdfutil.extract_pdf_text(path) or ""
    except Exception:
        try:
            from pypdf import PdfReader
            r = PdfReader(path)
            return "\n".join((p.extract_text() or "") for p in r.pages)
        except Exception:
            return ""


def is_core(rec):
    if not rec:
        return False
    return (rec.get("region") in CORE_REGIONS) and (rec.get("category") in CORE_CATS)


def main():
    t0 = time.time()
    kb = json.load(open(KB, encoding="utf-8"))
    by_cid = {}
    for r in kb:
        c = r.get("cid")
        if c:
            by_cid[c] = r

    pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    total_before = sum(os.path.getsize(p) for p in pdfs if os.path.exists(p))
    print("kb/pdf: %d 个, %.1f MB" % (len(pdfs), mb(total_before)))

    keep, prune, rescue = [], [], []
    for p in pdfs:
        size = os.path.getsize(p)
        cid = os.path.basename(p)[:-4]
        rec = by_cid.get(cid)
        core = is_core(rec)
        big = size > KEEP_MAX_MB * 1048576
        if core and not big:
            keep.append((p, size, cid, rec))
        else:
            rescue.append((p, size, cid, rec, core, big))

    print("  直接保留(A级且≤%.0fMB): %d 个 %.1f MB" % (KEEP_MAX_MB, len(keep), mb(sum(s for _, s, _, _ in keep))))
    print("  待抽文本后删: %d 个 %.1f MB" % (len(rescue), mb(sum(s for _, s, _, _, _, _ in rescue))))

    # 抽文本 → 删 PDF
    for p, size, cid, rec, core, big in rescue:
        fpath = os.path.join(FULL_DIR, cid + ".txt")
        txt = ""
        if os.path.exists(fpath):
            try:
                txt = open(fpath, encoding="utf-8", errors="replace").read()
            except Exception:
                txt = ""
        if len(txt) < 600:
            txt = extract_text(p)
            if len(txt) >= 600 and not DRY:
                os.makedirs(FULL_DIR, exist_ok=True)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(txt)
        if len(txt) >= 600:
            prune.append((p, size, cid, rec, core, big, len(txt)))
        else:
            keep.append((p, size, cid, rec))   # 抽不出文字，保留避免丢唯一原文

    saved = sum(s for _, s, _, _, _, _, _ in prune)
    print("  计划删除: %d 个 %.1f MB（已成功抽出文字）" % (len(prune), mb(saved)))

    if not DRY:
        for p, size, cid, rec, core, big, n in prune:
            try:
                os.remove(p)
            except Exception as e:
                print("   删除失败", p, repr(e)[:60])
                continue
            if rec is not None:
                rec["pdf_pruned"] = 1
                rec["pdf_size_mb"] = mb(size)
                why = "体积过大(%sMB)" % mb(size) if big else "非重点来源(学术/国际)"
                tail = ("；原 PDF 已按体积治理移除（%s），正文已抽取入库（%d 字），"
                        "原文请见来源链接" % (why, n))
                rec["note"] = (rec.get("note") or "").rstrip("；; ") + tail
                if rec.get("pdf"):
                    rec.pop("pdf", None)

    # ── 总预算控制：仍超预算则继续裁（非核心优先、体积从大到小）──
    kept_pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    cur = sum(os.path.getsize(p) for p in kept_pdfs if os.path.exists(p))
    print("  治理后 kb/pdf: %d 个 %.1f MB（预算 %.0f MB）" % (len(kept_pdfs), mb(cur), BUDGET_MB))
    if cur > BUDGET_MB * 1048576:
        cands = []
        for p in kept_pdfs:
            cid = os.path.basename(p)[:-4]
            rec = by_cid.get(cid)
            core = is_core(rec)
            cands.append((0 if core else 1, -os.path.getsize(p), p, cid, rec))
        cands.sort()
        for _, nsz, p, cid, rec in cands:
            if cur <= BUDGET_MB * 1048576:
                break
            size = os.path.getsize(p)
            fpath = os.path.join(FULL_DIR, cid + ".txt")
            ok = os.path.exists(fpath) and os.path.getsize(fpath) >= 600
            if not ok:
                t = extract_text(p)
                if len(t) >= 600 and not DRY:
                    os.makedirs(FULL_DIR, exist_ok=True)
                    open(fpath, "w", encoding="utf-8").write(t)
                    ok = True
            if not ok:
                continue
            if not DRY:
                try:
                    os.remove(p)
                except Exception:
                    continue
                if rec is not None:
                    rec["pdf_pruned"] = 1
                    rec["pdf_size_mb"] = mb(size)
                    rec["note"] = (rec.get("note") or "").rstrip("；; ") + \
                        "；原 PDF 已按总体积预算移除，正文已抽取入库，原文见来源链接"
                    rec.pop("pdf", None)
            cur -= size
        print("  预算裁剪后: %.1f MB" % mb(cur))

    # ── 清理历史快照页 ──
    removed_pages, page_bytes = 0, 0
    for f in os.listdir(BASE):
        if PAGE_EXT.match(f):
            fp = os.path.join(BASE, f)
            try:
                sz = os.path.getsize(fp)
                if not DRY:
                    os.remove(fp)
                removed_pages += 1
                page_bytes += sz
            except Exception:
                pass
    print("  清理历史快照页: %d 个 %.1f MB" % (removed_pages, mb(page_bytes)))

    if not DRY:
        _safe_dump(KB, kb)

    after = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    after_size = sum(os.path.getsize(p) for p in after if os.path.exists(p))
    print("=" * 56)
    print("prune_repo: PDF %d -> %d 个；%.1f MB -> %.1f MB；另清页面 %.1f MB；用时 %.1f min"
          % (len(pdfs), len(after), mb(total_before), mb(after_size), mb(page_bytes),
             (time.time() - t0) / 60.0))


if __name__ == "__main__":
    main()

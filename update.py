# -*- coding: utf-8 -*-
"""统一更新入口：归一化 -> 合并 inbox -> kb -> 富化 -> 标注 -> 抽全文 -> 图谱 -> 分片 -> 覆盖率 -> 渲染。
供定时任务/回填任务一键调用。
用法：python update.py
"""
import os, runpy, json, shutil, time

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
KB_BAK = os.path.join(BASE, "kb", "kb.json.guard")


def _kb_count(path=KB):
    try:
        with open(path, encoding="utf-8") as f:
            return len(json.load(f))
    except Exception:
        return -1


def _guard_check(stage, before):
    """kb 完整性护栏：条目数骤降或损坏则从备份恢复（防写坏/清空）"""
    after = _kb_count()
    if before > 200 and (after < 0 or after < before * 0.5):
        print("!! [kb护栏] %s 后条目数异常（%d -> %d），从备份恢复" % (stage, before, after))
        try:
            shutil.copy(KB_BAK, KB)
            print("  已恢复，当前 %d 条" % _kb_count())
        except Exception as e:
            print("  恢复失败：%s" % repr(e)[:80])
        return _kb_count()
    return after


# 起始备份
if os.path.exists(KB):
    try:
        shutil.copy(KB, KB_BAK)
        print("[update] kb 起始 %d 条，已备份" % _kb_count())
    except Exception as e:
        print("[update] 备份失败：%s" % repr(e)[:80])


def run(name, optional=False):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        if optional:
            print("[update] 跳过（不存在）: %s" % name)
            return
        raise FileNotFoundError(p)
    print("\n===== update: %s =====" % name)
    _before = _kb_count()
    try:
        runpy.run_path(p, run_name="__main__")
    finally:
        _guard_check(name, _before)


# 1) 字段归一化（region/department 受控词表 + DOI 规范化）
run("normalize.py")
# 2) 合并 inbox -> kb（去重：规范化全标题 sha1 + URL 判重）
run("merge.py")
# 3) 富化：重要性 / 原有术语（保留兼容）
run("enrich_importance.py")
run("enrich_kg.py", optional=True)
# 3.4) 抽全文：长正文 -> kb/full/<cid>.txt（必须早于 annotate / kg_build，它们要读正文）
run("extract_fulltext.py")
# 3.5) 自动标注：业务域 topic / 文号 doc_no / 层级 / 归口部门 / 地域 / 水体 / 时效 / 重要性
run("annotate.py", optional=True)
# 3.52) 法规原文抓取与校核（按 law_sources.json 修复「有链接无原文」；
#       校验含"第一条"且条文数≥15 且文本≥3000 字，不达标一律标记「暂未收录」）
if os.path.exists(os.path.join(BASE, "crawl_law.py")):
    os.environ["LAW_APPLY"] = "1"
    run("crawl_law.py", optional=True)
# 3.55) 内容准入判定：按内容类型打标（政策/报告/案例/专家/技术/学术/资讯）
#       —— 学术文献按期刊分级准入（journal_tier），政策/案例类放宽以确保不遗漏；
#          只打标不删除（gate_status=reject 供门户过滤与噪声治理清单）
run("gate.py", optional=True)
# 3.6) 知识图谱：结构化实体-关系网络 -> kb/graph.json
run("kg_build.py", optional=True)
# 3.7) 分片：kb.json -> kb/shards/* + meta.json（支撑海量条目、按需加载）
run("shard.py")
# 3.8) 覆盖率看板：按"内容有效性"统计（严格口径 fulltext/total）-> kb/coverage.json
run("coverage.py", optional=True)
# 4) 渲染站点（静态索引/图谱数据）
run("render.py")

print("\nupdate done")

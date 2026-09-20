# -*- coding: utf-8 -*-
"""统一更新入口：归一化 -> 合并 inbox -> kb -> 富化 -> 标注 -> 抽全文 -> 图谱 -> 分片 -> 覆盖率 -> 渲染。
供定时任务/回填任务一键调用。
用法：python update.py
"""
import os, runpy

BASE = os.path.dirname(os.path.abspath(__file__))


def run(name, optional=False):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        if optional:
            print("[update] 跳过（不存在）: %s" % name)
            return
        raise FileNotFoundError(p)
    print("\n===== update: %s =====" % name)
    runpy.run_path(p, run_name="__main__")


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
# 3.6) 知识图谱：结构化实体-关系网络 -> kb/graph.json
run("kg_build.py", optional=True)
# 3.7) 分片：kb.json -> kb/shards/* + meta.json（支撑海量条目、按需加载）
run("shard.py")
# 3.8) 覆盖率看板：按"内容有效性"统计（严格口径 fulltext/total）-> kb/coverage.json
run("coverage.py", optional=True)
# 4) 渲染站点（静态索引/图谱数据）
run("render.py")

print("\nupdate done")

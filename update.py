# -*- coding: utf-8 -*-
"""统一更新入口：归一化 -> 合并 inbox -> kb，再富化渲染。供定时任务/回填任务一键调用。
用法：python update.py
"""
import os, runpy

BASE = os.path.dirname(os.path.abspath(__file__))
# 1) 字段归一化（region/department 受控词表）
runpy.run_path(os.path.join(BASE, "normalize.py"), run_name="__main__")
# 2) 合并 inbox -> kb（去重）
runpy.run_path(os.path.join(BASE, "merge.py"), run_name="__main__")
# 3) 富化：重要性 / 知识图谱术语（需 content，故在抽全文之前）
runpy.run_path(os.path.join(BASE, "enrich_importance.py"), run_name="__main__")
runpy.run_path(os.path.join(BASE, "enrich_kg.py"), run_name="__main__")
# 3.5) 抽全文：长正文 -> kb/full/<cid>.txt，kb.json 仅留轻量元数据（支撑海量条目）
runpy.run_path(os.path.join(BASE, "extract_fulltext.py"), run_name="__main__")
# 3.6) 分片：kb.json -> kb/shards/* + meta.json + terms.json（支撑百万级、按需加载）
runpy.run_path(os.path.join(BASE, "shard.py"), run_name="__main__")
# 3.7) 覆盖率看板：按"内容有效性"统计原文覆盖（严格口径 fulltext/total），写 kb/coverage.json
if os.path.exists(os.path.join(BASE, "coverage.py")):
    runpy.run_path(os.path.join(BASE, "coverage.py"), run_name="__main__")
# 4) 渲染站点（index.html 运行时 fetch kb.json，render 生成静态索引/图谱数据）
runpy.run_path(os.path.join(BASE, "render.py"), run_name="__main__")

print("update done")

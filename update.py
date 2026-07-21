# -*- coding: utf-8 -*-
"""统一更新入口：合并 inbox -> kb，再渲染站点。供每日任务/回填任务一键调用。
用法：python update.py
"""
import os, runpy

BASE = os.path.dirname(os.path.abspath(__file__))
for step in ("merge.py", "enrich_importance.py", "enrich_kg.py", "render.py"):
    runpy.run_path(os.path.join(BASE, step), run_name="__main__")

# 说明：真实发布年（含学术库未来预发表年份 2027 等）作为记录保留在 r["date"]；
# 仅当用于「年份分布 / 时间排序 / 时间筛选」时，前端按 effDate 规则回退到入库日，
# 因此此处不再强制清空未来年份。
print("update done")

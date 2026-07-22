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
# 3) 富化：重要性 / 知识图谱术语
runpy.run_path(os.path.join(BASE, "enrich_importance.py"), run_name="__main__")
runpy.run_path(os.path.join(BASE, "enrich_kg.py"), run_name="__main__")
# 4) 渲染站点（index.html 运行时 fetch kb.json，render 生成静态索引/图谱数据）
runpy.run_path(os.path.join(BASE, "render.py"), run_name="__main__")

print("update done")

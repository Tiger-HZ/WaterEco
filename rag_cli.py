# -*- coding: utf-8 -*-
"""双碳知识库 · RAG 命令行工具（纯标准库）。
命令：
  python rag_cli.py search "查询" [--topk 8] [--dept 生态环境] [--cat policy] [--qmin A] [--from 2025-01-01] [--to 2026-12-31] [--no-eco]
  python rag_cli.py ask   "全国碳市场覆盖哪些行业？"
  python rag_cli.py report "杭州市 碳达峰试点" [--topk 12]
  python rag_cli.py stats
示例（自动化报告生成）：
  python rag_cli.py report "碳排放双控" --topk 15 > report_$(date +%F).md
"""
import sys, argparse, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rag import RAG

CATS = ["policy", "standard", "carbon_market", "synergy", "industry", "tech", "literature", "intl_region"]


def main():
    p = argparse.ArgumentParser(description="双碳知识库 RAG 检索/问答/报告")
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("search")
    ps.add_argument("query")
    ps.add_argument("--topk", type=int, default=8)
    ps.add_argument("--dept")
    ps.add_argument("--cat", choices=CATS)
    ps.add_argument("--region")
    ps.add_argument("--qmin", choices=["A", "B", "C"])
    ps.add_argument("--date-from")
    ps.add_argument("--date-to")
    ps.add_argument("--no-eco", action="store_true")

    pa = sub.add_parser("ask")
    pa.add_argument("query")
    pa.add_argument("--topk", type=int, default=6)

    pr = sub.add_parser("report")
    pr.add_argument("topic")
    pr.add_argument("--topk", type=int, default=12)

    sub.add_parser("stats")
    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    r = RAG().build()

    if args.cmd == "stats":
        print("知识库记录数：", len(r.records))
        print("检索分块数：", len(r.chunks))
        print("已抓取全文：", sum(1 for x in r.records if x.get("content_fetched")))
        return

    if args.cmd == "search":
        flt = {k: v for k, v in dict(dept=args.dept, cat=args.cat, region=args.region,
                                      quality_min=args.qmin,
                                      date_from=args.date_from,
                                      date_to=args.date_to).items() if v}
        hits = r.search(args.query, top_k=args.topk, filters=flt, prefer_eco=not args.no_eco)
        for i, h in enumerate(hits, 1):
            print("%d. [%.4f] %s" % (i, h["score"], h["title"]))
            print("   %s | %s | %s | 质量%s" % (h["department"], h["category"], h["date"], h["quality"]))
            print("   片段：%s" % h["chunk"][:140])
            print("   链接：%s\n" % h["url"])
        return

    if args.cmd == "ask":
        res = r.ask(args.query, top_k=args.topk)
        print("【回答】\n" + res["answer"])
        print("\n【引用来源】")
        for c in res["citations"][:8]:
            print(" - [%s] %s  (%s)" % (c["department"], c["title"], c["url"]))
        return

    if args.cmd == "report":
        print(r.report(args.topic, top_k=args.topk))
        return


if __name__ == "__main__":
    main()

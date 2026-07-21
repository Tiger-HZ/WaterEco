# -*- coding: utf-8 -*-
"""通过 GitHub Git Database API（api.github.com，沙箱可达）提交变更到仓库。
用于沙箱 git push 被网络拦截时的兜底部署。
相比 Contents API（单文件 ≤1MB），Git Database API 的 blob 上限 100MB，可支撑数万条 kb.json。
【安全】Token 绝不写在本文件。从环境变量 GH_TOKEN 或本地未跟踪文件 .deploy_token 读取。
用法：python gh_deploy.py
"""
import os, json, base64, time, re, urllib.request, urllib.error, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = "Tiger-HZ/WaterEco"
BRANCH = "main"

# Token：优先环境变量，其次本地未跟踪文件（已被 .gitignore 忽略，不会进仓库）
TOKEN = os.environ.get("GH_TOKEN", "")
if not TOKEN:
    tokfile = os.path.join(BASE, ".deploy_token")
    if os.path.exists(tokfile):
        TOKEN = open(tokfile, encoding="utf-8").read().strip()
if not TOKEN:
    raise SystemExit("未找到 GitHub Token：请设置环境变量 GH_TOKEN 或在 .deploy_token 中写入。")

FILES = [
    "kb/kb.json",
    "rag.html", "rag.py", "rag_cli.py", "fetch_fulltext.py", "ingest_url.py", "README_RAG.md",
    ".gitignore", "update.py", "render.py", "enrich_importance.py", "enrich_kg.py",
    "config.json", "gh_deploy.py", "crawl_gov.py", "crawl_weixin.py", "crawl_academic.py",
    "index.html", "feed.html", "archive.html", "docs/知识库建设方案.md",
]
for f in sorted(os.listdir(BASE)):
    if f.startswith("push-") and f.endswith(".html"):
        FILES.append(f)

API = "https://api.github.com/repos/%s" % REPO


def req(method, url, data=None):
    r = urllib.request.Request(url, headers={
        "Authorization": "Bearer %s" % TOKEN,
        "Accept": "application/vnd.github+json",
        "User-Agent": "dual-carbon-deploy",
        "Content-Type": "application/json",
    })
    r.get_method = lambda: method
    if data is not None:
        r.data = data.encode("utf-8")
    return urllib.request.urlopen(r, timeout=180)


def get_base_commit():
    """返回 (commit_sha, tree_sha)。"""
    with req("GET", API + "/git/refs/heads/%s" % BRANCH) as r:
        ref = json.load(r)
    csha = ref["object"]["sha"]
    with req("GET", API + "/git/commits/%s" % csha) as r:
        com = json.load(r)
    return csha, com["tree"]["sha"]


def list_tree_recursive(tree_sha):
    """递归列出当前树中的所有 blob：{path: sha}。"""
    out = {}
    with req("GET", API + "/git/trees/%s?recursive=1" % tree_sha) as r:
        d = json.load(r)
    for e in d.get("tree", []):
        if e.get("type") == "blob":
            out[e["path"]] = e["sha"]
    return out


def create_blob(text):
    """创建 blob，返回 sha。大文件也能处理。"""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    for attempt in range(6):
        try:
            with req("POST", API + "/git/blobs", json.dumps({"content": b64, "encoding": "base64"})) as r:
                return json.load(r)["sha"]
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")
            print("  blob HTTP %d %s" % (e.code, msg[:120]))
            time.sleep(3)
        except Exception as e:
            print("  blob err %s %s" % (type(e).__name__, str(e)[:80]))
            time.sleep(3)
    raise SystemExit("blob 创建失败（文件过大或被限流）")


def main():
    csha, tsha = get_base_commit()
    remote = list_tree_recursive(tsha)
    local_set = set(FILES)

    entries = []
    n_new = 0
    # 1) 本地变更/新增文件 → 创建 blob
    for p in sorted(local_set):
        lp = os.path.join(BASE, p)
        if not os.path.exists(lp):
            print("MISSING", p)
            continue
        with open(lp, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        sha = create_blob(text)
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": sha})
        n_new += 1
        print("blob %5d KB  %s" % (len(text) // 1024, p))
        time.sleep(0.15)
    # 2) 远程仍存在、但本地未列出的文件：保留；稀疏旧 push 页删除
    n_keep = 0
    n_del = 0
    for path, sha in remote.items():
        if path in local_set:
            continue
        if re.match(r"^push-.*\.html$", path):
            n_del += 1  # 不加入 tree = 删除
            continue
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
        n_keep += 1

    # 3) 创建新树（完整替换，无 base_tree）
    with req("POST", API + "/git/trees", json.dumps({"tree": entries})) as r:
        new_tree = json.load(r)["sha"]
    # 4) 创建提交
    n_kb = 0
    try:
        n_kb = len(json.load(open(os.path.join(BASE, "kb/kb.json"), encoding="utf-8")))
    except Exception:
        pass
    msg = "deploy: %d changed + %d kept, deleted %d stale push pages; kb=%d records" % (n_new, n_keep, n_del, n_kb)
    with req("POST", API + "/git/commits", json.dumps({"message": msg, "tree": new_tree, "parents": [csha]})) as r:
        new_commit = json.load(r)["sha"]
    # 5) 更新分支引用（触发 GitHub Pages 构建）
    with req("PATCH", API + "/git/refs/heads/%s" % BRANCH, json.dumps({"sha": new_commit})) as r:
        print("ref update HTTP %d" % r.status)

    print("\n=== 部署完成：本地 %d 文件已提交，保留 %d，删除陈旧推送页 %d，知识库 %d 条 ===" % (n_new, n_keep, n_del, n_kb))


if __name__ == "__main__":
    main()

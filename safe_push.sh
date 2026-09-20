#!/usr/bin/env bash
# 健壮提交与推送（safe_push）
# ---------------------------------------------------------------------------
# 背景（2026-09-20）：auto.yml / refill.yml / sites.yml / bulk.yml 都往同一个仓库推，
#   虽然共用 concurrency 组，但手动 dispatch、API 提交（deploy.py）仍可能造成
#   "non-fast-forward" 推送被拒 —— auto.yml 的「提交入库数据」步因此在
#   2026-09-19 连续两次 failure。
#
# 本脚本做三件事：
#   1) 提交（无变更则空提交心跳，保持仓库活跃、停更可被察觉）
#   2) 推送失败时自动 fetch + merge(-X ours) 后重试，最多 5 次
#      —— 冲突时保留本地版本：本地是本次刚重新生成/合并过的 kb，语义正确
#   3) 始终 exit 0：推送失败不得阻断后续「上传产物 / 部署 Pages」，
#      否则会出现"采集成功但站点不更新"的静默停更。
#
# 用法：bash safe_push.sh "auto|refill|sites|bulk"
# ---------------------------------------------------------------------------
set -u

TAG="${1:-update}"
BRANCH="${GITHUB_REF_NAME:-main}"

git config user.email "bot@water-eco.local"
git config user.name "WaterEco Bot"
date +%FT%T%z > last_run.txt

git add -A
if git diff --cached --quiet; then
  git commit --allow-empty -m "${TAG}: 无变更 $(date +%F-%H:%M) UTC" || true
else
  git commit -m "${TAG}: $(date +%F-%H:%M) UTC" || true
fi

for i in 1 2 3 4 5; do
  if git push origin "HEAD:${BRANCH}"; then
    echo "[safe_push] push 成功（第 ${i} 次尝试）"
    exit 0
  fi
  echo "[safe_push] push 被拒（第 ${i} 次），fetch + merge 后重试"
  git fetch origin "${BRANCH}" || true
  # 冲突一律保留本地（本次刚生成）版本
  git merge -X ours --no-edit "origin/${BRANCH}" || {
    git merge --abort 2>/dev/null || true
  }
  sleep 5
done

echo "[safe_push] 5 次重试仍失败，放弃推送（不阻断部署）"
exit 0

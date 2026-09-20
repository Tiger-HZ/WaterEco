#!/usr/bin/env bash
# 健壮提交与推送（v2，2026-09-20）
#
# 为什么需要：
#   多个工作流共用 concurrency，但手动 dispatch / 外部 API 提交仍会与定时任务撞车，
#   `git push` 报 non-fast-forward；若该步骤没有 continue-on-error，会直接 failure
#   并连带「上传产物」失败（实测 run 35448708687）。
#
# v1 的缺陷（必须避免）：
#   用 `git merge -X ours origin/main` 处理冲突时，"ours" 是 **runner 上的旧代码**，
#   会把上游在本次任务运行期间推送的**新代码/新数据覆盖掉**
#   （实测会回退门户新栏目、坏链修正、yml 改动）。
#
# v2 策略：
#   1) 先直接 push；
#   2) 被拒则同步远端，**代码文件永远以远端为准**（reset --hard origin），
#      只把本次任务刚生成的「数据目录」(DATA_DIRS) 保留后放回，再提交推送；
#   3) 无论成功与否 **始终 exit 0** —— 推送失败绝不阻断「上传产物 / 部署 Pages」，
#      杜绝「采集成功但站点不更新」的静默停更。
#
# 用法：bash safe_push.sh <tag>
#   例：bash safe_push.sh auto
#   数据目录可用环境变量定制：DATA_DIRS="kb docs"（默认 "kb"）

set -u

TAG="${1:-update}"
BR="${GITHUB_REF_NAME:-main}"
DATA_DIRS="${DATA_DIRS:-kb}"

git config user.email "bot@water-eco.local"
git config user.name "WaterEco Bot"
date +%FT%T%z > last_run.txt
git add -A

if git diff --cached --quiet; then
  git commit --allow-empty -m "${TAG}: 无变更 $(date +%F-%H:%M) UTC" || true
else
  git commit -m "${TAG}: $(date +%F-%H:%M) UTC" || true
fi

for i in 1 2 3; do
  if git push origin "HEAD:${BR}"; then
    echo "[safe_push] push 成功（第 ${i} 次）"
    exit 0
  fi
  echo "[safe_push] push 被拒（第 ${i} 次），同步远端后重试"

  if ! git fetch origin "${BR}"; then
    echo "[safe_push] fetch 失败，稍后重试"
    sleep 5
    continue
  fi

  # 远端领先：保留本次生成的数据目录，其余（代码/配置）一律以远端为准
  TMP="$(mktemp -d)"
  for d in ${DATA_DIRS}; do
    if [ -e "${d}" ]; then cp -a "${d}" "${TMP}/" 2>/dev/null || true; fi
  done

  if ! git reset --hard "origin/${BR}"; then
    echo "[safe_push] reset 失败，放弃本轮推送（不阻断部署）"
    rm -rf "${TMP}"
    exit 0
  fi

  for d in ${DATA_DIRS}; do
    base="$(basename "${d}")"
    if [ -e "${TMP}/${base}" ]; then
      rm -rf "${d}"
      cp -a "${TMP}/${base}" "${d}"
    fi
  done
  rm -rf "${TMP}"

  git add -A
  if git diff --cached --quiet; then
    git commit --allow-empty -m "${TAG}: 已同步远端，无数据变更 $(date +%F-%H:%M) UTC" || true
  else
    git commit -m "${TAG}: 同步远端后提交数据 $(date +%F-%H:%M) UTC" || true
  fi
  sleep 3
done

echo "[safe_push] 推送最终未成功（不阻断后续部署）"
exit 0

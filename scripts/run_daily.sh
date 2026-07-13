#!/usr/bin/env bash
# 本机每日运行入口（替代已停用的 GitHub Actions daily.yml）。
# 步骤：feed→curate（run-feed）→ archive+合并 Telegram+记 ledger（run-archive）→ 提交 ledger/站点数据并 push。
# 由 crontab 每天调用；机器长开机。日志写到 logs/daily_<date>.log。
#
# 关键容错（对齐旧 CI 语义 + 持久化工作树的额外防护）：
#   - run-archive 即使 IMA 失败也会先推 Telegram 并记 pushed_ledger，再以非零退出；
#     所以无论其成败都必须 commit+push（防止次日重复推送）。故不用 `set -e`，显式捕获退出码。
#   - 只提交本流水线产出的指定文件（显式 pathspec），不牵连工作树里其他改动。
#   - 跑前 ff-only 同步远端；fetch/ff 失败即中止（避免基于陈旧状态提交、push 非 fast-forward 积压）。
#   - commit/push 失败以非零退出让 cron 可见；上次残留的未推 commit 会在下次补推。
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PY="$REPO_DIR/.venv/bin/python"
LOG_DIR="$REPO_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily_$(date +%F).log"

exec >>"$LOG" 2>&1
log() { echo "[$(date '+%F %T')] $*"; }
log "===== run_daily start ====="

# 只提交这些文件（+ 当天 selected_*.json）；显式 pathspec，避免把其他暂存/改动一起 commit。
ARTIFACTS=(
  data/digests/pushed_ledger.json
  data/digests/archived_ledger.json
  docs/data/papers.json
)

# 载入 .env（含 LLM/Telegram/IMA 凭据 + NODE_BIN）。
if [[ ! -f "$REPO_DIR/.env" ]]; then
  log "FATAL: .env not found at $REPO_DIR/.env"
  exit 3
fi
set -a
# shellcheck disable=SC1091
source "$REPO_DIR/.env"
set +a

# push origin main 推的是本地 main：先确认确实在 main 上。
cur_branch="$(git symbolic-ref --short HEAD 2>/dev/null || echo DETACHED)"
if [[ "$cur_branch" != "main" ]]; then
  log "FATAL: not on main (on '$cur_branch'); refuse to run."
  log "===== run_daily end (branch-guard) ====="
  exit 6
fi

# 跑前把本地 main 与远端对齐（ff-only）。本机是唯一写入方，正常应始终可 ff。
# fetch 或 ff 失败都中止：避免基于陈旧远端提交、之后 push 非 fast-forward 反复失败、本地积压 commit。
log "--- sync with origin (ff-only) ---"
if ! git fetch origin main; then
  log "FATAL: git fetch failed; aborting to avoid stale-state commits."
  log "===== run_daily end (fetch-fail) ====="
  exit 4
fi
if ! git merge --ff-only origin/main; then
  log "FATAL: local main diverged from origin/main; cannot fast-forward. Resolve manually."
  log "===== run_daily end (sync-fail) ====="
  exit 4
fi

# feed + curate（不发 Telegram）。失败则整日中止（无 selected 可归档）。
log "--- run-feed ---"
"$PY" -m bio_2_info run-feed
feed_rc=$?
if [[ $feed_rc -ne 0 ]]; then
  log "run-feed failed (rc=$feed_rc); aborting (nothing to archive/push)."
  log "===== run_daily end (feed-fail) ====="
  exit $feed_rc
fi

# archive + 合并 Telegram + 记 ledger。IMA 抖动会返回非零，但 Telegram/ledger 已处理。
log "--- run-archive ---"
"$PY" -m bio_2_info run-archive
archive_rc=$?
[[ $archive_rc -ne 0 ]] && log "run-archive returned rc=$archive_rc (likely IMA hiccup; digest still pushed)."

# 提交 ledger + 站点数据：防止次日重复推送、让 Pages 更新。
log "--- commit + push ---"
shopt -s nullglob
selected_files=(data/digests/selected_*.json)
shopt -u nullglob

stage=()
for f in "${selected_files[@]}" "${ARTIFACTS[@]}"; do
  [[ -e "$f" ]] && stage+=("$f")
done

push_rc=0
if [[ ${#stage[@]} -gt 0 ]]; then
  if ! git add -f "${stage[@]}"; then
    log "FATAL: git add failed."
    log "===== run_daily end (add-fail) ====="
    exit 5
  fi
fi

if [[ ${#stage[@]} -gt 0 ]] && ! git diff --cached --quiet -- "${stage[@]}"; then
  if git commit -m "chore: daily $(date -u +%F)" -- "${stage[@]}"; then
    :
  else
    push_rc=$?
    log "git commit failed (rc=$push_rc)."
  fi
else
  log "no changes to commit."
fi

# push 条件：有未推到远端的 commit（新 commit 或上次 push 失败残留）。用 if/else 取真实退出码。
if [[ $push_rc -eq 0 ]] && [[ -n "$(git rev-list origin/main..main 2>/dev/null)" ]]; then
  if git push origin main; then
    :
  else
    push_rc=$?
    log "git push failed (rc=$push_rc); commit created but not pushed (will retry next run)."
  fi
fi

# 退出码：push/commit 失败优先暴露，否则反映 archive 结果。
final_rc=$archive_rc
[[ $push_rc -ne 0 ]] && final_rc=$push_rc
log "===== run_daily end (feed=$feed_rc archive=$archive_rc push=$push_rc) ====="
exit $final_rc

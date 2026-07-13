# bio_2_info

每日生信文献的「收集 → AI 精筛 → 归档 → 推送」流水线。由本机 crontab 每天定点运行；GitHub 仅作代码托管与 Pages 展示。

## 流程

```
PubMed + bioRxiv         LLM (Zhipu GLM 4.6 等)        IMA「每日生信资讯」KB         Telegram「研究资讯」频道
─────────────────  ───►  ─────────────────────  ───►  ─────────────────────  ───►  ───────────────────────
   feed.py                 curate.py                    archive.py                    notify.py
   候选 JSON               精筛+中文总结 JSON             下载OA PDF + 上传digest        渲染Markdown并发送
```

本机 `scripts/run_daily.sh` 由 crontab 每天调用：`run-feed`（feed+curate，不发 Telegram）→
`run-archive`（归档 IMA + 推**一条合并消息** + 记 ledger）→ 把 ledger/站点数据 commit 并 push 回
`origin`。两阶段在同一进程内串行，`selected_<today>.json` 经本地磁盘传递，无需 git 中转。

| 阶段命令                  | 作用 |
|--------------------------|------|
| `bio-2-info run-feed`    | 抓取 + LLM 精筛（不发 Telegram） |
| `bio-2-info run-archive` | 归档 IMA + 推一条合并消息（摘要+归档状态）+ 记 ledger |

## 快速开始

```bash
git clone <repo>
cd bio_2_info
cp .env.example .env  # 然后填好 LLM_API_KEY / TELEGRAM_BOT_TOKEN
pip install -e ".[dev]"

# 加载 .env 并跑全流程（本地手动验证）
set -a; source .env; set +a
bio-2-info feed              # 1) 抓候选
bio-2-info curate            # 2) LLM 精筛
bio-2-info archive --skip-ima  # 3) 只生成 digest 不传 IMA
bio-2-info run-feed --dry-run   # 整套联调（不真的发 Telegram）
```

## 配置（.env）

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` | OpenAI-compatible LLM 的 key（当前用 GPT-5.5 中转；代码默认仍是 Zhipu GLM） |
| `LLM_BASE_URL` | 当前 `https://api.tfclab-logan.xyz/v1`（代码默认 `https://open.bigmodel.cn/api/paas/v4`） |
| `LLM_MODEL` | 当前 `gpt-5.5`（代码默认 `glm-4.6`） |
| `NODE_BIN` | node 绝对路径；cron 净环境无 fnm shell init，须显式指定 |
| `TELEGRAM_BOT_TOKEN` | 与 ssq-checker 共用的 bot token |
| `TELEGRAM_CHAT_ID` | 默认 `-1004407117408`（「研究资讯」频道） |
| `IMA_CLIENT_ID` | IMA OpenAPI client id |
| `IMA_API_KEY` | IMA OpenAPI api key |
| `IMA_KB_NAME` | 默认 `每日生信资讯` |
| `BIO_SKIP_IMA` | 设为 `1` 跳过 IMA 上传（仅生成 digest） |

## 部署（本机 cron）

不再用 GitHub Actions（原托管账号被封）。改为本机 crontab 每天调用 `scripts/run_daily.sh`，
凭据放本机 `.env`（不进 git）。安装 crontab（本机时区 Asia/Hong_Kong，每天 08:00）：

```bash
crontab -e
# 加一行：
0 8 * * * /home/logan/Projects/bio_2_info/scripts/run_daily.sh
```

脚本会跑 run-feed → run-archive，然后把 ledger（`pushed_ledger.json`/`archived_ledger.json`）
与 `docs/data/papers.json` commit 并 push 回 `origin`；日志写在 `logs/daily_<date>.log`。
GitHub Pages 用「Deploy from branch」（`main` 的 `/docs`）自动发布，无需任何 workflow。

## 依赖

- **运行时**：Python ≥ 3.10，纯 stdlib（urllib/json/argparse），无 pip 依赖。
- **IMA 上传**：Node ≥ 18（用 vendored `vendor/ima/*.cjs`，无 npm 依赖）。
- **测试**：`pytest`（`pip install -e ".[dev]"`）。

## 设计要点

- **零 runtime Python deps**：跟 ssq-checker 一样，避免 CI 装包失败。
- **LLM 后端可换**：默认 Zhipu，但只要 OpenAI-compatible Chat Completions 都能用。
- **跨日去重（两本账）**：均按 DOI（无则标题）记录、commit 回仓库供后续 run 复用：
  - `archived_ledger.json`：已归档到 IMA 的论文，避免重复传 PDF。
  - `pushed_ledger.json`：已推送到 Telegram 的论文。`feed` 抓取后会先剔除已推送的再交给 LLM 精筛，
    所以同一篇不会跨天重复推送；只记录"真正精选并发出"的，当天没选中的以后仍可被选。`--dry-run` 不记录。
- **Skip-IMA 模式**：CI 失败/无凭证时仍能跑通流程，digest 留在 artifact 里手动补传。
- **vendor IMA 工具**：脱离 `~/.hermes/skills/ima-skill/`，仓库自给自足。

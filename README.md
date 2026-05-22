# GBP Watcher (GitHub Actions 版)

每 15 分钟自动运行一次，抓取中国银行「英镑现汇卖出价」，按提醒规则发 Telegram 通知，并把最新的 `state.json` 提交回仓库，避免重复提醒。

## 你需要做什么

1. 新建 GitHub 仓库（比如：`gbp-watcher-telegram`）
2. 把本项目文件上传到仓库（保持 `.github/workflows/gbp-watcher.yml` 路径不变）
3. 配置 Secrets（仓库 `Settings → Secrets and variables → Actions`）：
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. 开启写入权限（仓库 `Settings → Actions → General → Workflow permissions` 选择 `Read and write permissions`）

## 本地运行（可选）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="xxx"
export TELEGRAM_CHAT_ID="xxx"
python main.py
```

## 配置说明

- `config.yaml`：
  - `thresholds`：触发阈值（数值越小越“便宜”）
  - `run_window`：北京时间 09:00–23:00 才会实际抓取并通知（其余时间只更新 `last_checked_at`）
  - 提醒节奏：09/13/17/21 固定提醒；11/15/19/23 仅在跌破更低 `0.01` 档时提醒；跌破 `9.11 / 9.00 / 8.95 / 8.90` 会立即提醒；从当日最低点反弹超过 0.5% 也会提醒

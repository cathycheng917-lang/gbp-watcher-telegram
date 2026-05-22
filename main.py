from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional

import requests
import yaml
from bs4 import BeautifulSoup

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "state.json"


@dataclass(frozen=True)
class Thresholds:
    watch: float
    target: float
    lower_1: float
    strong: float


ALERT_ORDER = ["watch", "target", "lower_1", "strong"]


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    with STATE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f) or {}


def save_state(state: dict[str, Any]) -> None:
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def parse_hhmm(s: str) -> time:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(s).strip())
    if not m:
        raise ValueError(f"Invalid time format: {s!r} (expected HH:MM)")
    hh = int(m.group(1))
    mm = int(m.group(2))
    return time(hour=hh, minute=mm)


def now_in_tz(tz_name: str) -> datetime:
    if ZoneInfo is None:
        return datetime.now()
    return datetime.now(ZoneInfo(tz_name))


def within_window(now: datetime, start_s: str, end_s: str) -> bool:
    start_t = parse_hhmm(start_s)
    end_t = parse_hhmm(end_s)
    now_t = now.timetz().replace(tzinfo=None)
    if start_t <= end_t:
        return start_t <= now_t <= end_t
    return now_t >= start_t or now_t <= end_t


def normalize_number(s: str) -> Optional[float]:
    s = str(s).strip()
    s = s.replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def fetch_boc_rate(url: str, currency_keywords: list[str], field_keywords: list[str]) -> float:
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("BOC page: table not found")

    rows = table.find_all("tr")
    if not rows:
        raise RuntimeError("BOC page: empty table")

    header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    if not header_cells:
        raise RuntimeError("BOC page: header not found")

    wanted_col = None
    for idx, name in enumerate(header_cells):
        for kw in field_keywords:
            if kw and kw in name:
                wanted_col = idx
                break
        if wanted_col is not None:
            break
    if wanted_col is None:
        raise RuntimeError(f"BOC page: cannot find field column by keywords: {field_keywords}")

    for r in rows[1:]:
        cells = [c.get_text(strip=True) for c in r.find_all(["th", "td"])]
        if not cells:
            continue
        row_joined = " ".join(cells)
        if any(kw in row_joined for kw in currency_keywords):
            if wanted_col >= len(cells):
                raise RuntimeError("BOC page: row has insufficient columns")
            rate = normalize_number(cells[wanted_col])
            if rate is None:
                raise RuntimeError(f"BOC page: cannot parse rate value: {cells[wanted_col]!r}")
            return rate

    raise RuntimeError(f"BOC page: currency row not found by keywords: {currency_keywords}")


def thresholds_from_config(cfg: dict[str, Any]) -> Thresholds:
    t = cfg.get("thresholds") or {}
    return Thresholds(
        watch=float(t["watch"]),
        target=float(t["target"]),
        lower_1=float(t["lower_1"]),
        strong=float(t["strong"]),
    )


def decide_level(rate: float, th: Thresholds) -> Optional[str]:
    if rate <= th.strong:
        return "strong"
    if rate <= th.lower_1:
        return "lower_1"
    if rate <= th.target:
        return "target"
    if rate <= th.watch:
        return "watch"
    return None


def level_rank(level: Optional[str]) -> int:
    if level is None:
        return -1
    return ALERT_ORDER.index(level)


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    resp.raise_for_status()


def main() -> int:
    cfg = load_config()
    state = load_state()

    run_window_cfg = cfg.get("run_window") or {}
    tz_name = str(run_window_cfg.get("tz") or "Asia/Shanghai")
    start_s = str(run_window_cfg.get("start") or "07:00")
    end_s = str(run_window_cfg.get("end") or "23:00")

    now = now_in_tz(tz_name)
    if not within_window(now, start_s, end_s):
        print(f"Skip: outside run window ({tz_name} {start_s}-{end_s}). now={now.isoformat()}")
        state["last_checked_at"] = now.isoformat()
        save_state(state)
        return 0

    source = cfg.get("source") or {}
    url = str(source.get("url") or "https://www.boc.cn/sourcedb/whpj/")
    currency_keywords = list(source.get("currency_keywords") or ["英镑", "GBP"])
    field_keywords = list(source.get("field_keywords") or ["现汇卖出价", "Selling Rate"])

    rate = fetch_boc_rate(url=url, currency_keywords=currency_keywords, field_keywords=field_keywords)

    th = thresholds_from_config(cfg)
    level = decide_level(rate, th)

    today = now.date().isoformat()
    last_alert_level = state.get("last_alert_level")
    last_alert_date = state.get("last_alert_date")

    # 每天重新允许提醒（避免昨天的状态影响今天）
    if last_alert_date != today:
        last_alert_level = None
        state["last_alert_level"] = None
        state["last_alert_date"] = today

    state["last_rate"] = rate
    state["last_checked_at"] = now.isoformat()

    if level is None:
        print(f"GBP rate={rate:.4f} (no alert)")
        save_state(state)
        return 0

    # 只在“更强”的提醒出现时再发一次：watch -> target -> lower_1 -> strong
    should_alert = level_rank(level) > level_rank(last_alert_level)
    if not should_alert:
        print(f"GBP rate={rate:.4f} level={level} (already alerted: {last_alert_level})")
        save_state(state)
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars (GitHub Secrets).")

    text = (
        f"GBP 现汇卖出价触发提醒：{level}\n"
        f"当前：{rate:.4f}\n"
        f"阈值：watch={th.watch}, target={th.target}, lower_1={th.lower_1}, strong={th.strong}\n"
        f"时间：{now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )
    send_telegram(token=token, chat_id=chat_id, text=text)

    state["last_alert_level"] = level
    state["last_alert_date"] = today
    save_state(state)

    print(f"Sent alert. GBP rate={rate:.4f} level={level}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


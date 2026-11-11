"""段7 推送层：ntfy.sh + Bearer Token。

接口（01_INTERFACES 3.1）：
    Publisher.publish(report, json_url) -> bool
report 只要求 dict 或具备 date/summary_markdown/items 属性的对象。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date as _date
from pathlib import Path

MAX_BYTES = 4096        # 总 UTF-8 字节上限（S 约束）
BODY_CHAR_MIN = 1000    # 正文中文字下限
BODY_CHAR_MAX = 1200    # 正文中文字上限
RETRIES = 3             # 失败重试次数
RETRY_BACKOFF = 2.0     # 秒，指数退避基数
DEDUP_FILE = Path(os.environ.get("PUBLISHER_DEDUP_FILE", "publish_state.json"))
CATEGORIES = ["民生", "经济金融", "科技产业", "安全国防", "政务法治", "其他"]


def _field(report, name, default=None):
    if isinstance(report, dict):
        return report.get(name, default)
    return getattr(report, name, default)


def build_body(report, json_url: str, title_tpl: str = "民生政策日报 {date}") -> tuple[str, str]:
    """返回 (title, body)；body 已按字符数与字节数裁剪，末尾带 json_url。"""
    report_date = str(_field(report, "date", "") or "")
    title = title_tpl.format(date=report_date) if report_date else "民生政策日报"

    md = str(_field(report, "summary_markdown", "") or "").strip()
    if not md:
        md = _fallback_markdown(report)

    tail = f"\n\nJSON: {json_url}" if json_url else ""
    budget = MAX_BYTES - len(tail.encode("utf-8"))

    # 先按字符上限截，再按字节上限截，两者取小
    text = md[:BODY_CHAR_MAX]
    while text and len(text.encode("utf-8")) > budget:
        cut = int(len(text) * budget / max(1, len(text.encode("utf-8")))) - 1
        text = text[:max(0, cut)]
    return title, text + tail


def _fallback_markdown(report) -> str:
    """无 summary_markdown 时，用分类+标题+摘要拼正文。"""
    items = _field(report, "items", []) or []
    counts = {c: 0 for c in CATEGORIES}
    for it in items:
        cat = it.get("category") if isinstance(it, dict) else _field(it, "category")
        counts[cat] = counts.get(cat, 0) + 1
    lines = ["，".join(f"{c}{n}" for c, n in counts.items() if n) or "今日暂无更新"]
    for cat in CATEGORIES:
        group = [i for i in items
                 if (i.get("category") if isinstance(i, dict) else _field(i, "category")) == cat]
        if not group:
            continue
        lines.append(f"\n## {cat}")
        for it in group:
            get = (lambda k: it.get(k, "")) if isinstance(it, dict) else (lambda k: _field(it, k, ""))
            lines.append(f"- **{get('title')}**：{get('summary')}（{get('source')}）")
    return "\n".join(lines)


def _load_state() -> dict:
    try:
        return json.loads(DEDUP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _already_sent(today: str) -> bool:
    return _load_state().get("last_sent_date") == today


def _mark_sent(today: str) -> None:
    try:
        DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEDUP_FILE.write_text(json.dumps({"last_sent_date": today}), encoding="utf-8")
    except OSError as exc:
        print(f"[publisher] 记录去重状态失败: {exc}", file=sys.stderr)


class NtfyPublisher:
    """推送实现。配置从构造参数或环境变量读取，token 永不打印。"""

    def __init__(self, server: str = "https://ntfy.sh",
                 topic: str | None = None,
                 token: str | None = None,
                 token_env: str = "NTFY_TOKEN",
                 topic_env: str = "NTFY_TOPIC",
                 title_template: str = "民生政策日报 {date}",
                 timeout_seconds: float = 15.0,
                 retries: int = RETRIES,
                 force: bool = False):
        self.server = server.rstrip("/")
        self.topic = topic or os.environ.get(topic_env, "").strip()
        self.token = token or os.environ.get(token_env, "").strip()
        self.title_template = title_template
        self.timeout = timeout_seconds
        self.retries = retries
        self.force = force

    def publish(self, report, json_url: str) -> bool:
        today = str(_field(report, "date", "") or _date.today().isoformat())
        if not self.force and _already_sent(today):
            print(f"[publisher] {today} 已推送过，跳过（同日去重）")
            return True
        if not self.topic:
            print("[publisher] 缺少 ntfy topic（环境变量），跳过推送", file=sys.stderr)
            return False

        title, body = build_body(report, json_url, self.title_template)
        if len(body.encode("utf-8")) > MAX_BYTES:
            print("[publisher] 正文超出 4096 字节，放弃推送", file=sys.stderr)
            return False

        headers = {
            "Title": _encode_header(title),
            "Priority": "default",
            "Tags": "memo",
            "Content-Type": "text/plain; charset=utf-8",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"   # 只进请求头，不进日志

        url = f"{self.server}/{self.topic}"
        payload = body.encode("utf-8")

        for attempt in range(1, self.retries + 1):
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        _mark_sent(today)
                        print(f"[publisher] 推送成功 {today} ({len(payload)} 字节)")
                        return True
                    print(f"[publisher] HTTP {resp.status}，第 {attempt} 次", file=sys.stderr)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # 只打印异常类型与原因，绝不拼接 headers
                print(f"[publisher] 推送失败（第 {attempt}/{self.retries} 次）: {exc}", file=sys.stderr)
            if attempt < self.retries:
                time.sleep(RETRY_BACKOFF * attempt)
        return False


def _encode_header(text: str) -> str:
    """HTTP 头只允许 latin-1；非 ASCII 走 RFC 2047 编码，否则原样。"""
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        import base64
        return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def get_publisher(name: str = "ntfy", **kwargs):
    if name == "noop":
        return _NoopPublisher()
    return NtfyPublisher(**kwargs)


class _NoopPublisher:
    def publish(self, report, json_url: str) -> bool:
        print("[publisher] noop：未发送")
        return True


def _self_check() -> None:
    long = "中国" * 2000 + "\n## 分类\n" + "- 条目" * 300
    report = {"date": "2026-11-11", "summary_markdown": long, "items": []}
    title, body = build_body(report, "https://example.com/reports/2026-11-11.json")
    assert len(body.encode("utf-8")) <= MAX_BYTES, "超出字节上限"
    assert body.endswith("https://example.com/reports/2026-11-11.json"), "JSON 地址必须保留"
    assert "token" not in body.lower()

    r2 = {"date": "2026-11-11", "items": [
        {"category": "民生", "title": "T", "summary": "S", "source": "中国政府网"},
        {"category": "其他", "title": "T2", "summary": "S2", "source": "X"}]}
    _, b2 = build_body(r2, "https://e/x.json")
    assert "## 民生" in b2 and "其他1" in b2

    assert _encode_header("民生政策日报").startswith("=?UTF-8?B?")
    print("self-check ok")


if __name__ == "__main__":
    _self_check()

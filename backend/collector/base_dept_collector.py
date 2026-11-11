"""段4 直采基类：单部门 HTML 直采骨架。

子类只实现 dept_name / list_url / parse_list / fetch_detail 四个钩子，
collect 由基类统一编排：列表 → 去重 → 抓正文 → 返回段3 同构的容器列表。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

try:  # 包内导入 / 脚本直跑
    from .collector_unified import UA, _norm, _to_date
except ImportError:  # pragma: no cover
    from collector_unified import UA, _norm, _to_date

log = logging.getLogger(__name__)

TIMEOUT = 15
RETRIES = 3
# ponytail: 同域名固定 1.5s 间隔，与段3一致；被限流才升级为自适应退避
DOMAIN_INTERVAL = 1.5

# 段3 同款正文容器（顺序即优先级）
TEXT_CONTAINERS = (
    "div.TRS_Editor",
    "div.article-content",
    "div#UCAP-CONTENT",
    "div.pages_content",
    "article",
)
MIN_TEXT_LEN = 80


class _Throttle:
    def __init__(self, interval: float = DOMAIN_INTERVAL):
        self.interval = interval
        self._last: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last.get(host)
        if last is not None:
            gap = self.interval - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last[host] = time.monotonic()


_throttle = _Throttle()


class BaseDeptCollector(ABC):
    """一个部门 = 一个子类。collect(date) 返回段3 容器列表：

    [{'title', 'url', 'department', 'publish_date', 'summary', 'raw_text'}]
    """

    source_name: str = ""  # 落 JSON 的 source 名，缺省用 dept_name

    def __init__(self, timeout: float = TIMEOUT, retries: int = RETRIES, transport=None):
        self.client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": UA, "Referer": "https://www.gov.cn/"},
            transport=transport,
            follow_redirects=True,
        )
        self.retries = retries

    # ---------- 钩子 ----------
    @property
    @abstractmethod
    def dept_name(self) -> str:
        """部门全称，如“人力资源和社会保障部”。"""

    @abstractmethod
    def list_url(self, date: str) -> str:
        """返回 date 当天列表页 URL。"""

    @abstractmethod
    def parse_list(self, html: str, date: str) -> list[dict]:
        """解析列表页，返回本条收集器口径的条目（至少含 title/url）。"""

    @abstractmethod
    def fetch_detail(self, item: dict) -> str:
        """抓取并返回单条正文纯文本；失败返回空串。"""

    # ---------- 公共能力 ----------
    def get(self, url: str, **kw) -> httpx.Response | None:
        for attempt in range(1, self.retries + 1):
            try:
                _throttle.wait(url)
                r = self.client.get(url, **kw)
                if r.status_code < 400:
                    return r
            except httpx.HTTPError:
                pass
            if attempt < self.retries:
                time.sleep(attempt)
        return None

    def extract_text(self, html: str) -> str:
        """按段3 容器列表抓正文，命中第一条 ≥80 字即返回。"""
        if not html or "<" not in html:
            return ""
        tree = HTMLParser(html)
        for sel in TEXT_CONTAINERS:
            node = tree.css_first(sel)
            if node is None:
                continue
            text = node.text(separator="\n", strip=True)
            if len(text) >= MIN_TEXT_LEN:
                return text
        return ""

    def normalize_item(self, item: dict, date: str) -> dict:
        """补齐段3 容器字段。子类 parse_list 产出不完整也安全。"""
        return {
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "department": item.get("department") or self.dept_name,
            "publish_date": _to_date(item.get("publish_date") or "") or date,
            "summary": (item.get("summary") or "").strip(),
            "raw_text": item.get("raw_text") or "",
        }

    # ---------- 主流程 ----------
    def collect(self, date: str, fulltext: bool = True, limit: int | None = None) -> list[dict]:
        """单部门失败只记日志返回空列表，绝不抛到上层。"""
        try:
            r = self.get(self.list_url(date))
            if r is None:
                log.warning("%s 列表页抓取失败: %s", self.dept_name, self.list_url(date))
                return []
            rows = self.parse_list(r.text, date) or []
        except Exception:  # noqa: BLE001 —— 单部门异常隔离
            log.exception("%s 列表解析异常", self.dept_name)
            return []

        seen_url: set[str] = set()
        seen_title: set[tuple[str, str]] = set()
        kept: list[dict] = []
        for row in rows:
            try:
                it = self.normalize_item(row, date)
            except Exception:  # noqa: BLE001
                log.exception("%s 条目归一化异常", self.dept_name)
                continue
            if not it["title"] or not it["url"]:
                continue
            soft = (_norm(it["title"]), it["publish_date"])
            if it["url"] in seen_url or soft in seen_title:
                continue
            seen_url.add(it["url"])
            seen_title.add(soft)
            kept.append(it)
            if limit is not None and len(kept) >= limit:
                break

        if fulltext:
            for it in kept:
                try:
                    it["raw_text"] = self.fetch_detail(it) or ""
                except Exception:  # noqa: BLE001 —— 正文失败不影响条目
                    log.exception("%s 正文抓取异常: %s", self.dept_name, it["url"])
                    it["raw_text"] = ""
        log.info("%s %s items=%d", self.dept_name, date, len(kept))
        return kept

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

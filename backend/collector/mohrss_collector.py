"""段4 人社部直采示例。

列表：http://www.mohrss.gov.cn/SYrlzyhshbzb/zcfg/（或政策文件栏目）
正文：按段3 容器列表抽取（div.TRS_Editor 等）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

try:  # 包内导入 / 脚本直跑
    from .base_dept_collector import BaseDeptCollector
except ImportError:  # pragma: no cover
    from base_dept_collector import BaseDeptCollector

BASE = "http://www.mohrss.gov.cn"
LIST_URL = BASE + "/SYrlzyhshbzb/zcfg/"

# 列表行：<li><a href="...">标题</a><span>2026-11-11</span></li>
_LI_SELECTOR = "ul.list li, div.list li, li"
_DATE_IN_TEXT = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")


def normalize_date(s: str) -> str:
    """'2026年11月11日' / '2026/11/11' / '2026-11-11' → 'YYYY-MM-DD'；无日期返回空串。"""
    m = _DATE_IN_TEXT.search(s or "")
    if not m:
        return ""
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


class MohrssCollector(BaseDeptCollector):
    source_name = "人力资源和社会保障部"

    @property
    def dept_name(self) -> str:
        return "人力资源和社会保障部"

    def list_url(self, date: str) -> str:
        return LIST_URL

    def parse_list(self, html: str, date: str) -> list[dict]:
        out: list[dict] = []
        for li in HTMLParser(html).css(_LI_SELECTOR):
            a = li.css_first("a")
            if a is None:
                continue
            title = a.text(strip=True)
            href = (a.attributes.get("href") or "").strip()
            if not title or not href:
                continue
            row_text = li.text(separator=" ", strip=True)
            out.append({
                "title": title,
                "url": urljoin(BASE, href),
                "publish_date": normalize_date(row_text),
            })
        return out

    def fetch_detail(self, item: dict) -> str:
        r = self.get(item["url"])
        if r is None:
            return ""
        return self.extract_text(r.text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="段4 人社部直采示例")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--no-fulltext", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"bad --date: {args.date}", file=sys.stderr)
        return 2

    c = MohrssCollector()
    try:
        items = c.collect(args.date, fulltext=not args.no_fulltext, limit=args.limit)
    finally:
        c.close()

    print(f"{args.date} {c.dept_name} items={len(items)}")
    for it in items[:3]:
        print(json.dumps({
            "title": it["title"],
            "url": it["url"],
            "publish_date": it["publish_date"],
            "raw_text_len": len(it["raw_text"]),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

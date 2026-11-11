"""段3 采集统一入口：sousuo.www.gov.cn/search-gov/data（政策库 → 部门文件）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

API = "https://sousuo.www.gov.cn/search-gov/data"
UA = "DailyPolicyDigest/1.0"
TIMEOUT = 15
RETRIES = 3
PAGE_SIZE = 100
# ponytail: 同域名固定 1.5s 间隔，够用；被限流才升级为自适应退避
DOMAIN_INTERVAL = 1.5

# 部门白名单（全称）
DEPARTMENTS = [
    "国务院办公厅", "国务院",
    "国家发展和改革委员会", "教育部", "工业和信息化部", "公安部", "民政部",
    "司法部", "财政部", "人力资源和社会保障部", "自然资源部", "生态环境部",
    "住房和城乡建设部", "交通运输部", "水利部", "农业农村部", "商务部",
    "文化和旅游部", "国家卫生健康委员会", "退役军人事务部", "应急管理部",
    "中国人民银行", "国家市场监督管理总局", "国家金融监督管理总局",
    "国家医疗保障局", "国家能源局", "国家移民管理局", "国家药品监督管理局",
    "国家粮食和物资储备局", "国家邮政局",
    "中华全国总工会", "中华全国妇女联合会", "中国残疾人联合会",
]

# 常见简称 → 全称（值必须出现在 DEPARTMENTS 中）
DEPT_ALIASES = {
    "国办": "国务院办公厅",
    "发改委": "国家发展和改革委员会",
    "国家发改委": "国家发展和改革委员会",
    "人社部": "人力资源和社会保障部",
    "人保部": "人力资源和社会保障部",
    "人资部": "人力资源和社会保障部",
    "工信部": "工业和信息化部",
    "住建部": "住房和城乡建设部",
    "农业部": "农业农村部",
    "文旅部": "文化和旅游部",
    "卫健委": "国家卫生健康委员会",
    "国家卫健委": "国家卫生健康委员会",
    "卫计委": "国家卫生健康委员会",
    "退役军人部": "退役军人事务部",
    "应急部": "应急管理部",
    "环保部": "生态环境部",
    "央行": "中国人民银行",
    "人民银行": "中国人民银行",
    "市场监管总局": "国家市场监督管理总局",
    "市监总局": "国家市场监督管理总局",
    "金融监管总局": "国家金融监督管理总局",
    "银保监会": "国家金融监督管理总局",
    "医保局": "国家医疗保障局",
    "国家医保局": "国家医疗保障局",
    "药监局": "国家药品监督管理局",
    "粮储局": "国家粮食和物资储备局",
    "国家粮储局": "国家粮食和物资储备局",
    "粮食局": "国家粮食和物资储备局",
    "移民局": "国家移民管理局",
    "国家移民局": "国家移民管理局",
    "能源局": "国家能源局",
    "国家能源局": "国家能源局",
    "总工会": "中华全国总工会",
    "全国总工会": "中华全国总工会",
    "妇联": "中华全国妇女联合会",
    "中国残联": "中国残疾人联合会",
}

# 非白名单部门：标题/摘要命中则保留
LIVELIHOOD_KEYWORDS = [
    "医保", "医疗", "看病", "门诊", "住院", "药品", "疫苗", "养老", "退休", "社保",
    "参保", "工伤", "失业", "救助", "低保", "抚恤", "孤寡", "残疾", "就业", "招聘",
    "工资", "薪酬", "欠薪", "劳动", "托育", "育儿", "母婴", "妇幼", "儿童", "未成年",
    "学生", "学费", "助学", "家长", "教育", "入学", "学位", "高校", "中考", "高考",
    "教师", "住房", "租房", "公积金", "城中村", "小区", "供水", "供电", "供暖",
    "燃气", "饮用水", "食品安全", "消费者权益", "价格", "涨价", "补贴", "救灾",
    "救济", "困难群体", "保障性住房",
]

_NON_ALNUM = re.compile(r"[\s\u3000·・•\-—_/(),，。]")
_DIGITS = re.compile(r"\D")


def _norm(name: str) -> str:
    return _NON_ALNUM.sub("", name or "")


_WHITELIST = {_norm(d): d for d in DEPARTMENTS}
_ALIAS = {_norm(a): full for a, full in DEPT_ALIASES.items()}


def resolve_department(source: str) -> str | None:
    """source → 白名单全称；不在白名单返回 None。"""
    n = _norm(source)
    if not n:
        return None
    if n in _WHITELIST:
        return _WHITELIST[n]
    if n in _ALIAS:
        return _ALIAS[n]
    for key, full in _WHITELIST.items():
        if key in n:  # “国务院办公厅印发…”这类带前后缀的写法
            return full
    return None


def hits_livelihood(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t)
    return any(k in blob for k in LIVELIHOOD_KEYWORDS)


def _to_date(s: str) -> str:
    """'2026-11-11 10:00:00' / '2026/11/11' / '20261111' / 毫秒时间戳 → 'YYYY-MM-DD'。"""
    s = (s or "").strip()
    if not s:
        return ""
    if _DIGITS.sub("", s) == "" and len(s) >= 12:
        return datetime.fromtimestamp(int(s) / 1000).strftime("%Y-%m-%d")
    m = re.search(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})", s)
    if not m:
        return ""
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def _list_vo(payload: dict) -> list[dict]:
    try:
        return payload["searchVO"]["catMap"]["bumenfile"]["listVO"] or []
    except (KeyError, TypeError):
        return []


def _strip_html(text: str) -> str:
    if not text or "<" not in text:
        return (text or "").strip()
    return HTMLParser(text).text(separator="\n", strip=True)


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


class UnifiedCollector:
    def __init__(self, timeout: float = TIMEOUT, retries: int = RETRIES, transport=None):
        self.client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": UA, "Referer": "https://www.gov.cn/"},
            transport=transport,
            follow_redirects=True,
        )
        self.retries = retries

    def _get(self, url: str, **kw) -> httpx.Response | None:
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

    # ---------- 列表 ----------
    def list_items(self, date: str) -> list[dict]:
        """拉 date 当天（往前 24 小时）的部门文件。"""
        end = datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=1)
        params = {
            "t": "zhengcelibrary",
            "childtype": "bumenfile",
            "mintime": start.strftime("%Y-%m-%d"),
            "maxtime": end.strftime("%Y-%m-%d"),
            "sort": "pubtime",
            "n": PAGE_SIZE,
            "p": 0,
        }
        r = self._get(API, params=params)
        if r is None:
            return []
        try:
            payload = r.json()
        except ValueError:
            return []

        out: list[dict] = []
        for row in _list_vo(payload):
            source = (row.get("source") or "").strip()
            title = _strip_html(row.get("title") or "")
            summary = _strip_html(row.get("summary") or row.get("content") or "")
            url = (row.get("url") or "").strip()
            if not title or not url:
                continue
            dept = resolve_department(source)
            if dept is None:
                if not hits_livelihood(title, summary):
                    continue
                dept = source or "中国政府网"
            out.append({
                "title": title,
                "url": url,
                "department": dept,
                "publish_date": _to_date(row.get("pubtimeStr") or row.get("pubtime") or "") or date,
                "summary": summary,
                "raw_text": "",
            })
        return out

    # ---------- 正文 ----------
    SELECTORS = (
        "div.TRS_Editor",
        "div.article-content",
        "div#UCAP-CONTENT",
        "div.pages_content",
        "article",
    )

    def fetch_fulltext(self, url: str) -> str:
        if not url:
            return ""
        try:
            r = self._get(url)
            if r is None:
                return ""
            tree = HTMLParser(r.text)
            for sel in self.SELECTORS:
                node = tree.css_first(sel)
                if node is None:
                    continue
                text = node.text(separator="\n", strip=True)
                if len(text) >= 80:
                    return text
            return ""
        except Exception:  # noqa: BLE001 —— 采集失败不抛到上层
            return ""

    # ---------- 主流程 ----------
    def collect(self, date: str, fulltext: bool = True, limit: int | None = None) -> list[dict]:
        seen_url: set[str] = set()
        seen_title: set[tuple[str, str]] = set()
        kept: list[dict] = []
        for it in self.list_items(date):
            if it["url"] in seen_url:
                continue
            soft = (_norm(it["title"]), it["publish_date"])
            if soft in seen_title:
                continue
            seen_url.add(it["url"])
            seen_title.add(soft)
            kept.append(it)
            if limit is not None and len(kept) >= limit:
                break
        if fulltext:
            for it in kept:
                it["raw_text"] = self.fetch_fulltext(it["url"])
        return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="段3 采集统一入口")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD，取该日往前 24 小时")
    ap.add_argument("--no-fulltext", action="store_true", help="只列条目，不抓正文")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"bad --date: {args.date}", file=sys.stderr)
        return 2

    collector = UnifiedCollector()
    try:
        items = collector.collect(args.date, fulltext=not args.no_fulltext, limit=args.limit)
    finally:
        collector.client.close()

    print(f"{args.date} items={len(items)}")
    for it in items[:5]:
        print(json.dumps({
            "title": it["title"],
            "department": it["department"],
            "url": it["url"],
            "raw_text_len": len(it["raw_text"]),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

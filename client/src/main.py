"""民生政策日报客户端：Flet 单套代码（Android APK / Windows exe）。

只做：启动同步 + 前台每 5 分钟轮询 index.json → 补拉缺失日期 → 存 SQLite → 展示。
不爬取、不调模型、无 API Key、无后台常驻。
"""

import os
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import flet as ft

# ---------- 仅有的配置：JSON 源地址（S3：无 Key/token） ----------
BASE_URL = os.environ.get("POLICY_BASE_URL", "https://example.com/reports").rstrip("/")
POLL_SECONDS = 300
FETCH_TIMEOUT = 15
EMPTY_TEXT = "今日暂无更新"
OFFLINE_TEXT = "离线模式：网络不可用，显示本地缓存"

CATEGORIES = ["民生", "经济金融", "科技产业", "安全国防", "政务法治", "其他"]

C_MAIN, C_TEXT, C_SUB = "#1A56DB", "#1F2933", "#6B7280"
C_LINE, C_MUTED, C_SEL = "#E5E5E5", "#9CA3AF", "#E8EEFA"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (date TEXT PRIMARY KEY, generated_at TEXT,
  item_count INTEGER NOT NULL DEFAULT 0, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY,
  date TEXT NOT NULL REFERENCES reports(date) ON DELETE CASCADE,
  category TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', publish_date TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_items_date_cat ON items(date, category);
"""


# ---------- SQLite ----------
def db_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home())) / "PolicyDaily"
    else:  # Android 私有目录（H3）
        root = Path(os.environ.get("FLET_APP_STORAGE_DATA") or Path.home() / ".policydaily")
    root.mkdir(parents=True, exist_ok=True)
    return root / "reports.db"


class Db:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def dates(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT date FROM reports ORDER BY date DESC")]

    def has(self, date: str) -> bool:
        return self.conn.execute("SELECT 1 FROM reports WHERE date=?", (date,)).fetchone() is not None

    def save(self, report: dict) -> None:
        date = report["date"]
        with self.conn:
            self.conn.execute("DELETE FROM items WHERE date=?", (date,))
            self.conn.execute("DELETE FROM reports WHERE date=?", (date,))
            self.conn.execute(
                "INSERT INTO reports(date, generated_at, item_count, fetched_at) VALUES(?,?,?,?)",
                (date, report.get("generated_at", ""), len(report.get("items", [])),
                 datetime.now().isoformat(timespec="seconds")),
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO items(id,date,category,title,summary,source,url,publish_date)"
                " VALUES(?,?,?,?,?,?,?,?)",
                [(i["id"], date, i["category"], i["title"], i.get("summary", ""),
                  i.get("source", ""), i.get("url", ""), i.get("publish_date", date))
                 for i in report.get("items", [])],
            )

    def items(self, date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id,category,title,summary,source,url,publish_date FROM items"
            " WHERE date=? ORDER BY rowid", (date,))
        cols = ("id", "category", "title", "summary", "source", "url", "publish_date")
        return [dict(zip(cols, r)) for r in rows]


# ---------- HTTP ----------
def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "PolicyDaily/1.0"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        import json
        return json.loads(resp.read().decode("utf-8"))


def sync(db: Db) -> bool:
    """拉 index.json 并补拉缺失日期（H5）。返回是否在线。"""
    try:
        index = http_json(f"{BASE_URL}/index.json")
    except Exception:
        return False
    for date in index.get("dates", []):
        if db.has(date):
            continue
        try:
            db.save(http_json(f"{BASE_URL}/{date}.json"))
        except Exception:
            continue
    return True


# ---------- UI ----------
def main(page: ft.Page):
    page.title = "民生政策日报"
    page.bgcolor = "#FFFFFF"
    page.padding = 0
    page.window.width, page.window.height = 1100, 720
    page.window.min_width, page.window.min_height = 800, 500

    mobile = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
    db = Db(db_path())

    state = {"date": None, "cat": None}

    banner = ft.Text(OFFLINE_TEXT, size=12, color=C_SUB)
    banner_box = ft.Container(content=banner, visible=False,
                              padding=ft.padding.symmetric(6, 12),
                              bgcolor="#F3F4F6")

    date_col = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    cat_col = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    cat_row = ft.Row(spacing=8, scroll=ft.ScrollMode.AUTO,
                     height=44, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    items_col = ft.ListView(spacing=0, expand=True, padding=0)

    def line():
        return ft.Container(height=1, bgcolor=C_LINE)

    def refresh():
        dates = db.dates()
        if not dates:
            state["date"] = None
        elif state["date"] not in dates:
            state["date"] = dates[0]
        date = state["date"]

        # 日期栏
        if mobile:
            opts = [ft.dropdown.Option(d) for d in dates] or [
                ft.dropdown.Option("", EMPTY_TEXT)]
            date_col.controls = [ft.Dropdown(
                value=date, options=opts, width=220, content_padding=8,
                text_size=16, on_change=lambda e: pick_date(e.control.value))]
        else:
            date_col.controls = [
                ft.Container(
                    content=ft.Text(d, size=14, color=C_MAIN if d == date else C_TEXT,
                                    weight=ft.FontWeight.W_600 if d == date else None),
                    padding=ft.padding.symmetric(11, 14),
                    bgcolor=C_SEL if d == date else "#FFFFFF",
                    on_click=lambda e, d=d: pick_date(d),
                ) for d in dates
            ] or [ft.Container(content=ft.Text(EMPTY_TEXT, size=12, color=C_MUTED),
                               padding=14)]

        # 分类（空分类隐藏）
        its = db.items(date) if date else []
        cats = [c for c in CATEGORIES if any(i["category"] == c for i in its)]
        if state["cat"] not in cats:
            state["cat"] = None
        if mobile:
            cat_col.controls = [cat_row]
            cat_row.controls = [
                ft.Container(
                    content=ft.Text(f"{c}{sum(1 for i in its if i['category'] == c)}",
                                    size=12, color=C_MAIN if c == state["cat"] else C_TEXT),
                    padding=ft.padding.symmetric(6, 10),
                    bgcolor=C_SEL if c == state["cat"] else "#F9FAFB",
                    on_click=lambda e, c=c: pick_cat(c),
                ) for c in cats
            ]
        else:
            cat_col.controls = [
                ft.Container(
                    content=ft.Row([ft.Text(c, size=14, color=C_MAIN if c == state["cat"] else C_TEXT),
                                    ft.Text(str(sum(1 for i in its if i["category"] == c)),
                                            size=12, color=C_SUB)]),
                    padding=ft.padding.symmetric(11, 14),
                    bgcolor=C_SEL if c == state["cat"] else "#FFFFFF",
                    on_click=lambda e, c=c: pick_cat(c),
                ) for c in cats
            ]

        # 条目
        shown = [i for i in its if state["cat"] in (None, i["category"])]
        items_col.controls = [item_view(i) for i in shown] or [
            ft.Container(content=ft.Text(EMPTY_TEXT, size=12, color=C_MUTED),
                         alignment=ft.alignment.center, padding=40)]
        page.update()
        # ponytail: 全量刷新 + 每次重建控件，日报量级（≤20 条/天）足够，上千条再改增量

    def pick_date(d):
        state["date"] = d or None
        refresh()

    def pick_cat(c):
        state["cat"] = None if state["cat"] == c else c
        refresh()

    def open_url(url):
        if url:
            page.launch_url(url)

    def item_view(i: dict):
        return ft.Container(
            content=ft.Column([
                ft.Text(i["title"], size=16, weight=ft.FontWeight.W_600, color=C_MAIN,
                        on_click=lambda e, u=i["url"]: open_url(u)),
                ft.Text(i["summary"], size=14, color=C_TEXT, max_lines=3,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(f"{i['source']} · {i['publish_date']}", size=12, color=C_SUB),
                line(),
            ], spacing=6),
            padding=ft.padding.symmetric(12, 16),
        )

    def do_sync(_=None):
        online = sync(db)
        banner_box.visible = not online and bool(db.dates())
        refresh()

    if mobile:
        body = ft.Column([date_col, line(), cat_col, line(), items_col], spacing=0, expand=True)
    else:
        body = ft.Row([
            ft.Container(content=date_col, width=200, bgcolor="#FFFFFF"),
            ft.VerticalDivider(width=1, color=C_LINE),
            ft.Container(content=cat_col, width=120),
            ft.VerticalDivider(width=1, color=C_LINE),
            ft.Container(content=items_col, expand=True),
        ], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    page.add(banner_box,
             ft.Container(content=ft.Text("民生政策日报", size=16,
                                          weight=ft.FontWeight.W_600, color=C_TEXT),
                          padding=ft.padding.symmetric(10, 16)),
             line(), body)

    def tick():
        while True:
            import time
            time.sleep(POLL_SECONDS)  # 仅前台轮询（H4：不常驻后台）
            try:
                do_sync()
            except Exception:
                pass

    do_sync()
    import threading
    threading.Thread(target=tick, daemon=True).start()


if __name__ == "__main__":
    ft.app(target=main)

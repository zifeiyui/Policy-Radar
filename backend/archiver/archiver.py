"""段6 归档层：写 reports/YYYY-MM-DD.json + index.json，返回 HTTPS URL。

契约：archive(report) -> json_url
- 原子写：先 .tmp 再 os.replace（Windows 同盘 rename 亦原子覆盖）
- index.json：倒序（新→旧）、去重、保留 365 天
- 只写 JSON，不含 Key/token（S1/S2/S5）
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path

KEEP_DAYS = 365
ROOT = Path(__file__).resolve().parent
# 默认落盘目录：<APP>/reports；可用环境变量覆盖（部署时指向站点根目录）
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR") or ROOT.parent.parent / "reports")
# 默认公开前缀：GitHub Pages / Cloudflare Pages 项目站
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def _atomic_write(path: Path, text: str) -> None:
    """写同目录 .tmp 后 os.replace，读者永远看不到半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    # Windows：目标被占用时 replace 会短暂失败，重试几次
    for i in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 4:
                raise
            time.sleep(0.2)


def _to_dict(report) -> dict:
    if isinstance(report, dict):
        return report
    if hasattr(report, "to_dict"):
        return report.to_dict()
    if hasattr(report, "__dict__"):
        return dict(report.__dict__)
    raise TypeError("report 需为 dict 或含 to_dict() 的对象")


def _atomic_write_json(path: Path, data) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


# 由 app.py / 配置注入的公开前缀（archive() 可直接读全局，不强迫改签名）
public_base_url: str = PUBLIC_BASE_URL
reports_dir: Path = REPORTS_DIR


def _build_index(dates: list[str]) -> list[str]:
    """倒序去重 + 只留最近 365 天（按日期字符串比较，ISO 日期可直接比大小）。"""
    uniq = sorted({d for d in dates if len(d) == 10}, reverse=True)
    if not uniq:
        return []
    latest = date.fromisoformat(uniq[0])
    return [d for d in uniq if (latest - date.fromisoformat(d)).days < KEEP_DAYS]


def archive(report, date: str | None = None, base_url: str | None = None) -> str:
    """写 {REPORTS_DIR}/{date}.json 和 index.json，返回该 JSON 的 HTTPS 地址。"""
    data = _to_dict(report)
    day = date or data.get("date")
    if not day:
        raise ValueError("report 缺少 date")
    # 校验：防止路径穿越/非法日期拼进文件名
    date.fromisoformat(day)

    _atomic_write_json(reports_dir / f"{day}.json", data)

    index_path = reports_dir / "index.json"
    existing: list[str] = []
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8")).get("dates", [])
        except (ValueError, AttributeError):
            existing = []  # 索引损坏：以当前目录已有日报重建
    if not existing:
        existing = [p.stem for p in reports_dir.glob("*.json") if p.stem != "index"]

    _atomic_write_json(
        index_path,
        {
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dates": _build_index(existing + [day]),
        },
    )

    # 顺手清理超期日报（365 天外），失败不影响归档结果
    try:
        expired = set(existing) - set(_build_index(existing + [day]))
        for d in expired:
            (reports_dir / f"{d}.json").unlink(missing_ok=True)
    except OSError:
        pass

    base = (base_url or public_base_url).rstrip("/")
    return f"{base}/{day}.json"


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        reports_dir = Path(td)
        base_url = "https://pages.example.com/reports"
        r = {"date": "2026-11-11", "generated_at": "x", "item_count": 0,
             "category_summary": [], "items": []}
        url = archive(r)
        assert url == f"{base_url}/2026-11-11.json", url
        assert json.loads((Path(td) / "2026-11-11.json").read_text(encoding="utf-8"))["date"] == "2026-11-11"
        # 重复归档同一天：去重
        archive(r)
        idx = json.loads((Path(td) / "index.json").read_text(encoding="utf-8"))
        assert idx["dates"] == ["2026-11-11"], idx
        assert not list(Path(td).glob("*.tmp"))
        # 倒序 + 365 天截断：2025-01-01 与 2024-12-31 应被丢弃
        for d in ["2026-11-10", "2024-12-31", "2025-01-01"]:
            archive({**r, "date": d})
        idx = json.loads((Path(td) / "index.json").read_text(encoding="utf-8"))
        assert idx["dates"] == ["2026-11-11", "2026-11-10"], idx
        assert not (Path(td) / "2024-12-31.json").exists()
        print("archiver self-check OK")

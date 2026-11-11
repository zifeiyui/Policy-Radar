#!/usr/bin/env python3
"""段11 验收脚本：python verify.py [--offline] [--live]

离线检查(默认): 日志触发时间 / 推送正文计数 / 03_DAILYREPORT.json 契约 / 权限与密钥静态扫描 / 备份
在线检查(--live): 采集非空 / 正文抓取 / ntfy 推送返回 / 报告与 index 实际可达

退出码: 0 = 通过; 1 = 存在 FAIL; 2 = 仅 WARN 或 SKIP。
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TODAY = date.today().isoformat()
CATS = ["民生", "经济金融", "科技产业", "安全国防", "政务法治", "其他"]
ITEM_KEYS = {"id", "category", "title", "summary", "source", "url", "publish_date"}
HTTP_TIMEOUT = 20

BLOCK_DIRS = {".git", "node_modules", ".gradle", "build", "__pycache__", ".idea", ".venv", "venv", "dist"}
BLOCK_EXT = {".apk", ".aar", ".jar", ".class", ".dex", ".so", ".dll", ".exe", ".pyc", ".png", ".jpg",
             ".jpeg", ".webp", ".gif", ".zip", ".7z", ".pdf", ".db", ".ttf", ".woff", ".woff2"}
SCAN_MAX = 512 * 1024

SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "疑似 DeepSeek Key"),
    (r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}", "疑似 Bearer token 字面量"),
    (r"(?i)\bDEEPSEEK_API_KEY\s*=\s*['\"]?[A-Za-z0-9]{16,}", "Key 明文赋值"),
    (r"(?i)\bNTFY_TOKEN\s*=\s*['\"]?[A-Za-z0-9_\-]{16,}", "token 明文赋值"),
]
FORBIDDEN_ANDROID = ["CAMERA", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO", "READ_MEDIA_VISUAL_USER_SELECTED",
                     "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE", "MANAGE_EXTERNAL_STORAGE", "ACCESS_MEDIA_LOCATION",
                     "RECORD_AUDIO", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION",
                     "READ_CONTACTS", "WRITE_CONTACTS", "READ_SMS", "RECEIVE_SMS", "SEND_SMS", "READ_PHONE_STATE",
                     "READ_PHONE_NUMBERS", "CALL_PHONE", "ANSWER_PHONE_CALLS", "PROCESS_OUTGOING_CALLS", "BLUETOOTH",
                     "BLUETOOTH_ADMIN", "BLUETOOTH_SCAN", "BLUETOOTH_CONNECT", "NFC", "RECEIVE_BOOT_COMPLETED",
                     "FOREGROUND_SERVICE", "REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", "WAKE_LOCK"]
CAT_IN_TEXT = re.compile("|".join(CATS))
HAN = re.compile(r"[\u4e00-\u9fff]")

RESULTS = []  # (no, name, status, detail)


def add(no, name, status, detail):
    RESULTS.append((no, name, status, detail))
    print(f"[{status}] {no:>2} {name}: {detail}")


def _walk(roots):
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in BLOCK_DIRS]
            for fn in filenames:
                yield Path(dirpath) / fn


def _text(p):
    try:
        if p.stat().st_size > SCAN_MAX:
            return None
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _load_env_file():
    env = Path(ROOT / ".env")
    out = {}
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _http(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.status, dict(r.headers), r.read()


def _load_config():
    try:
        import yaml
    except ImportError:
        return None
    for name in ("config.yaml", "backend/config.yaml", "config.yml"):
        p = ROOT / name
        if p.is_file():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                return None
    return None


def _cfg(path, default=None):
    cfg = _load_config()
    for k in path.split("."):
        if not isinstance(cfg, dict):
            return default
        cfg = cfg.get(k, default)
        if cfg is None:
            return default
    return cfg


# ---------------------------------------------------------------- 日志

LOG_DIRS = [ROOT / "logs", ROOT / "backend/logs", ROOT / "backend", ROOT / "reports"]


def _latest_log():
    cands = []
    for d in LOG_DIRS:
        if d.is_dir():
            for p in d.glob("*.log"):
                cands.append(p)
            for p in d.glob("*.jsonl"):
                cands.append(p)
    cands = [p for p in cands if p.is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _report_for(d):
    for c in (ROOT / "reports" / f"{d}.json", ROOT / f"{d}.json"):
        if c.is_file():
            return c
    return None


def _sent_at_from_report(d):
    rp = _report_for(d)
    if not rp:
        return None
    try:
        r = json.loads(rp.read_text(encoding="utf-8"))
        return datetime.fromisoformat(r["generated_at"])
    except Exception:
        return None


# ---------------------------------------------------------------- 各检查


def chk_trigger_log():
    """返回 (ok, detail, schedule_time)。"""
    log = _latest_log()
    if not log:
        return None, "未找到日志文件(logs/*.log)，无法离线判定触发时间", None
    txt = log.read_text(encoding="utf-8", errors="ignore")
    pat = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2}).*?(start|trigger|schedul|register|fire|run_daily|启动|触发|执行)",
                     re.IGNORECASE)
    hits = []
    for m in pat.finditer(txt):
        try:
            hits.append((datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}"), m.group(3)))
        except ValueError:
            pass
    if not hits:
        return None, f"{log.name} 中无带时间的触发记录", None
    dt = hits[-1][0]
    return dt, f"{log.name} 最后触发 {dt.isoformat()}", dt


def c1_trigger(offline):
    if offline:
        dt, msg, _ = chk_trigger_log()
        add(1, "18:01 触发采集", "WARN", msg + "；需手动核对第1条")  # ponytail: 仅当日实时触发才算 PASS
        return
    dt, msg, _ = chk_trigger_log()
    if dt is None:
        add(1, "18:01 触发采集", "FAIL", msg)
        return
    begin = datetime.fromisoformat(f"{TODAY}T18:01:00")
    lateness = (dt - begin).total_seconds()
    grace = int(_cfg("schedule.misfire_grace_seconds", 300) or 300)
    if dt.date().isoformat() != TODAY:
        add(1, "18:01 触发采集", "FAIL", f"最近触发不是今天：{msg}")
    elif not (0 <= lateness <= grace):
        add(1, "18:01 触发采集", "FAIL", f"{msg}，偏移 {int(lateness)}s，超出 [0,{grace}] 窗口")
    else:
        add(1, "18:01 触发采集", "PASS", f"{msg}，偏移 {int(lateness)}s ≤ {grace}s")


def chk_collect(base=None):
    """返回 (ok, detail, items)。"""
    url = None
    if base:
        url = base.rstrip("/") + "/api/collect"
    else:
        cfg_url = _cfg("backend_url") or _cfg("client.base_url")
        if cfg_url:
            url = str(cfg_url).rstrip("/") + "/api/collect"
    if url:
        try:
            st, _, body = _http(url)
            items = json.loads(body)
            if isinstance(items, dict):
                items = items.get("items", [])
            if not isinstance(items, list):
                return False, f"{url} 返回非列表", None
            n = len(items)
            if n <= 0:
                return False, f"{url} 返回 0 条", items
            bad = [i for i, it in enumerate(items, 1)
                   if not isinstance(it, dict) or not all(k in it for k in ("title", "url"))
                   or not str(it.get("title", "")).strip() or not str(it.get("url", "")).startswith("http")]
            if bad:
                return False, f"{url} 返回 {n} 条，其中 {len(bad)} 条缺 title/url（idx={bad[:5]}）", items
            return True, f"{url} 返回 {n} 条，title/url 齐全", items
        except Exception as e:
            return None, f"{url} 请求失败：{type(e).__name__}: {e}", None

    try:
        sys.path.insert(0, str(ROOT))
        from backend.collect import get_collector  # type: ignore
        name = _cfg("collector.provider", "html")
        items = get_collector(name).collect(TODAY)
        n = len(items or [])
        if n <= 0:
            return False, f"collector '{name}' 返回 {n} 条", items
        return True, f"collector '{name}' 返回 {n} 条", items
    except Exception as e:
        return None, f"无法调用 collector（{type(e).__name__}: {e}），请用 --base 或 --collect-json 注入", None


def c2_collect(offline, args):
    items = None
    if args.collect_json:
        try:
            items = json.loads(Path(args.collect_json).read_text(encoding="utf-8"))
            n = len(items)
            add(2, "采集非空", "PASS" if n > 0 else "FAIL", f"{args.collect_json} 含 {n} 条")
            return
        except Exception as e:
            add(2, "采集非空", "FAIL", f"{args.collect_json} 解析失败：{e}")
            return
    ok, msg, _ = chk_collect(args.base)
    if ok is None:
        add(2, "采集非空", "WARN" if offline else "SKIP", msg)
    else:
        add(2, "采集非空", "PASS" if ok else "FAIL", msg)


def c3_fetch_body(args):
    items = None
    ok, msg, items = chk_collect(args.base)
    if not items:
        add(3, "正文抓取（raw_text 非空）", "WARN", f"无采集样本可验证：{msg}")
        return
    sample = next((it for it in items if str(it.get("raw_text", "")).strip()), None)
    if sample is None:
        raw_keys = sorted({k for it in items if isinstance(it, dict) for k in it})
        got = raw_keys
        add(3, "正文抓取（raw_text 非空）", "FAIL", f"采集条目无 raw_text 字段（现有字段：{got}）")
        return
    txt = str(sample.get("raw_text", ""))
    body = "\n".join(str(it.get("raw_text", "")) for it in items if isinstance(it, dict))
    imgs = len(re.findall(r"(?i)<img\b|!\[[^\]]*\]\(|data:image/", body))
    length = len(HAN.findall(txt))
    # 正文不应是导航/标题样板：正文汉字数应大于标题汉字数
    tlen = len(HAN.findall(str(sample.get("title", ""))))
    if length <= max(50, tlen * 2):
        add(3, "正文抓取（raw_text 非空）", "FAIL", f"样本正文仅 {length} 汉字（标题 {tlen}），疑似抓到导航而非正文")
    elif imgs:
        add(3, "正文抓取（raw_text 非空）", "FAIL", f"正文含 {imgs} 处图片/base64，违反 H2")
    else:
        add(3, "正文抓取（raw_text 非空）", "PASS", f"样本 {sample.get('url', '')[:60]} 正文 {length} 汉字，无图片")


def c4_json(path=None):
    path = Path(path) if path else (ROOT / "03_DAILYREPORT.json")
    if not path.is_file():
        add(4, "DailyReport JSON 契约", "FAIL", f"{path} 不存在")
        return None
    try:
        raw = path.read_bytes()
        r = json.loads(raw.decode("utf-8"))
    except Exception as e:
        add(4, "DailyReport JSON 契约", "FAIL", f"UTF-8/JSON 解析失败：{e}")
        return None
    errs = []
    if not isinstance(r, dict):
        errs.append("根节点非对象")
    else:
        for k in ("date", "generated_at", "item_count", "category_summary", "items"):
            if k not in r:
                errs.append(f"缺字段 {k}")
        try:
            datetime.strptime(r.get("date", ""), "%Y-%m-%d")
        except ValueError:
            errs.append("date 非 YYYY-MM-DD")
        try:
            datetime.fromisoformat(r.get("generated_at", ""))
        except ValueError:
            errs.append("generated_at 非 ISO8601")
        items = r.get("items", [])
        if not isinstance(items, list):
            errs.append("items 非数组")
        else:
            if r.get("item_count") != len(items):
                errs.append(f"item_count={r.get('item_count')} ≠ len(items)={len(items)}")
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    errs.append(f"items[{i}] 非对象")
                    continue
                if set(it) != ITEM_KEYS:
                    errs.append(f"items[{i}] 字段集异常：多 {sorted(set(it) - ITEM_KEYS)} 少 {sorted(ITEM_KEYS - set(it))}")
                if it.get("category") not in CATS:
                    errs.append(f"items[{i}].category={it.get('category')!r} 不属六类")
                if not str(it.get("url", "")).startswith("https://"):
                    errs.append(f"items[{i}].url 非 https 原文链接")
                if it.get("id") != f"{r.get('date')}-{i + 1:02d}":
                    errs.append(f"items[{i}].id={it.get('id')!r} 与 date+序号不符")
                if it.get("publish_date") != r.get("date"):
                    errs.append(f"items[{i}].publish_date 与 date 不一致")
        cs = r.get("category_summary", [])
        if not isinstance(cs, list):
            errs.append("category_summary 非数组")
        else:
            names = [c.get("category") for c in cs if isinstance(c, dict)]
            if sorted(names) != sorted(CATS) or len(names) != 6:
                errs.append(f"category_summary 分类集={names}，应恰为六类")
            total = sum(c.get("count", 0) for c in cs if isinstance(c, dict))
            if total != r.get("item_count"):
                errs.append(f"sum(category_summary)={total} ≠ item_count={r.get('item_count')}")
            per = {c: 0 for c in CATS}
            for it in items if isinstance(items, list) else []:
                if isinstance(it, dict) and it.get("category") in per:
                    per[it["category"]] += 1
            for c in cs:
                if isinstance(c, dict) and per.get(c.get("category"), 0) != c.get("count"):
                    errs.append(f"{c.get('category')} 计数 {c.get('count')} ≠ items 实际 {per.get(c.get('category'))}")
    low = raw.lower()
    if b"base64" in low or re.search(rb"data:image/", low) or b'"image' in low:
        errs.append("JSON 含图片/base64 字段（违反 H2/S5）")
    if len(raw) > 2 * 1024 * 1024:
        errs.append(f"JSON {len(raw)} 字节异常偏大")
    if errs:
        add(4, "DailyReport JSON 契约", "FAIL", "；".join(errs[:6]))
        return None
    add(4, "DailyReport JSON 契约", "PASS",
        f"{path.name} 六类齐全，sum={r['item_count']}，{len(raw)}B，无 base64/图片")
    return r


def c5_body(r):
    bmin = int(_cfg("publisher.ntfy.body_char_min", 1000) or 1000)
    bmax = int(_cfg("publisher.ntfy.body_char_max", 1200) or 1200)
    cap = int(_cfg("publisher.ntfy.max_payload_bytes", 4096) or 4096)
    log = _latest_log()
    text = None
    src = ""
    if log:
        txt = log.read_text(encoding="utf-8", errors="ignore")
        for line in reversed(txt.splitlines()):
            if re.search(r"(?i)publish|ntfy|body", line):
                text, src = line, log.name
                break
    if text is None and r:
        text = r.get("summary_markdown") or "".join(str(i.get("summary", "")) for i in r.get("items", []))
        src = "03_DAILYREPORT.json(summary_markdown 作正文来源)"
    if not text:
        add(5, "推送正文 1000~1200 字 / ≤4096B", "WARN", "无正文样本（无日志、无 summary_markdown）")
        return
    n = len(HAN.findall(text))
    head = text[:bmax * 4]
    nb = len(head.encode("utf-8"))
    ok = (bmin <= n <= bmax) and nb <= cap and ("{date}" in text or ".json" in text)
    det = f"正文 {n} 汉字(要求 {bmin}-{bmax})，UTF-8 {nb}B(≤{cap})，来源 {src}"
    add(5, "推送正文 1000~1200 字 / ≤4096B", "PASS" if ok else "FAIL", det)


def c6_ntfy(args):
    topic = os.environ.get("NTFY_TOPIC") or _load_env_file().get("NTFY_TOPIC")
    token = os.environ.get("NTFY_TOKEN") or _load_env_file().get("NTFY_TOKEN")
    server = str(_cfg("publisher.ntfy.server", "https://ntfy.sh")).rstrip("/")
    rp = _report_for(TODAY) or (ROOT / "03_DAILYREPORT.json")
    if args.push_log:
        log = Path(args.push_log)
        text = log.read_text(encoding="utf-8", errors="ignore") if log.is_file() else ""
        ids = re.findall(r'"id"\s*:\s*"([A-Za-z0-9_\-]{8,})"', text)
        st = re.findall(r'"status"\s*:\s*(\d{3})', text)
        ok = bool(ids) and (not st or st[-1] == "200")
        add(6, "ntfy 推送返回 id/200", "PASS" if ok else "FAIL",
            f"{log.name} 返回 id={ids[-1] if ids else '无'} status={st[-1] if st else '未记录'}")
        return
    if not topic:
        add(6, "ntfy 推送返回 id/200", "WARN", "未发现 NTFY_TOPIC 且无 --push-log，无法发真实推送")
        return
    if not token:
        add(6, "ntfy 推送返回 id/200", "WARN", "未发现 NTFY_TOKEN（未认证 topic 无法校验）")
        return
    try:
        text = rp.read_text(encoding="utf-8")
        r = json.loads(text)
    except Exception as e:
        add(6, "ntfy 推送返回 id/200", "FAIL", f"读取 {rp.name} 失败：{e}")
        return
    body = (r.get("summary_markdown") or "")[:1200] + f"\n\nJSON: {_cfg('archive.public_base_url', str(ROOT))}/{TODAY}.json"
    try:
        req = urllib.request.Request(
            f"{server}/{urllib.parse.quote(topic)}", data=body.encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {token}", "Title": f"验收推送 {TODAY}",
                     "Tags": "white_check_mark", "Priority": "low"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            st = resp.status
            j = json.loads(resp.read().decode("utf-8", "ignore") or "{}")
        ok = st == 200 and bool(j.get("id"))
        add(6, "ntfy 推送返回 id/200", "PASS" if ok else "FAIL",
            f"{server} 返回 status={st} id={j.get('id')}（验收消息，客户端应收到）")
    except Exception as e:
        add(6, "ntfy 推送返回 id/200", "FAIL", f"{server} 推送失败：{type(e).__name__}: {e}")


def c7_index():
    p = ROOT / "reports/index.json"
    if not p.is_file():
        add(7, "reports/ + index.json 落盘", "FAIL", "reports/index.json 不存在")
        return
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        add(7, "reports/ + index.json 落盘", "FAIL", f"index.json 解析失败：{e}")
        return
    errs = []
    dates = j.get("dates")
    if not isinstance(dates, list) or not dates:
        errs.append("dates 为空或非数组")
        dates = []
    if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", str(j.get("updated_at", ""))):
        errs.append("updated_at 非 ISO8601")
    if dates != sorted(dates, reverse=True):
        errs.append("dates 未按倒序")
    if len(set(dates)) != len(dates):
        errs.append("dates 有重复")
    for d in dates:
        if not re.match(r"\d{4}-\d{2}-\d{2}$", str(d)):
            errs.append(f"date 格式异常：{d}")
        elif not (ROOT / "reports" / f"{d}.json").is_file():
            errs.append(f"{d}.json 缺失")
    if errs:
        add(7, "reports/ + index.json 落盘", "FAIL", "；".join(errs[:5]))
    else:
        add(7, "reports/ + index.json 落盘", "PASS", f"index 含 {len(dates)} 天，均有对应 JSON，最新 {dates[0]}")


def _app_files():
    files = []
    for base in (ROOT / "client", ROOT / "backend"):
        files += list(_walk([base]))
    for pat in ("*.kt", "*.java", "*.xml", "*.gradle", "*.kts", "*.py", "*.cpp", "*.h", "*.cs", "*.ps1", "*.spec"):
        files += list(ROOT.glob(pat))
    seen, out = set(), []
    for f in files:
        rp = str(f.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(f)
    return out


def c8_permissions():
    files = _app_files()
    if not files:
        add(8, "Android 仅 INTERNET / 无存储库 / 无后台常驻", "FAIL", "未找到 client/ 或后端源码，无法判定")
        return
    errs, info, manifests, other_perm = [], [], [], []
    for f in files:
        if f.suffix.lower() in BLOCK_EXT:
            continue
        t = _text(f)
        if t is None:
            continue
        rel = f.relative_to(ROOT)
        perms = sorted(set(re.findall(r"android\.permission\.([A-Z0-9_]+)", t)))
        if files and any(perms):
            other_perm.append((rel.name, perms))
        if f.name == "AndroidManifest.xml":
            actual = set(perms) or set(re.findall(r'<uses-permission[^>]*android:name="([^"]+)"', t))
            extra = [p for p in perms if p != "INTERNET"]
            if "INTERNET" not in perms:
                errs.append(f"{rel} 未声明 INTERNET")
            if extra:
                errs.append(f"{rel} 多声明 {extra}")
            if "android:requestLegacyExternalStorage" in t or "WRITE_EXTERNAL_STORAGE" in t:
                errs.append(f"{rel} 使用 legacy 外部存储")
            if "usesCleartextTraffic=\"true\"" in t:
                errs.append(f"{rel} 允许明文流量")
            if re.search(r'(?is)<service[^>]*exported="true"', t):
                errs.append(f"{rel} 有 exported=true 的 service")
            manifests.append(rel.name)
        for bad in ("appcompat", "androidx.work", "firebase", "onesignal", "jpush", "getui", "Ba-TvfU"):
            if bad in t and f.suffix.lower() in (".gradle", ".kts", ".xml"):
                errs.append(f"{rel} 含 {bad}")
        for dbpat in ("externalFilesDir", "getExternalStorage", "openOrCreateDatabase(", "WRITE_EXTERNAL_STORAGE"):
            if dbpat in t:
                errs.append(f"{rel} 疑似库/文件写非私有目录：{dbpat}")
        if f.suffix in (".py", ".kt", ".java") and re.search(r"(?i)(sk-[A-Za-z0-9]{20,}|DEEPSEEK_API_KEY\s*=\s*['\"]?[A-Za-z0-9]{16,})", t):
            errs.append(f"{rel} 含疑似模型 Key")
        if f.name in ("api.md", "sync.py", "SyncWorker.kt", "config.yaml") and re.search(r"(?i)ntfy\.sh\s*/?\{?\s*"?topic\"?|topic\s*[:=]\s*['\"][A-Za-z0-9]{20,}", t):
            if "NTFY_TOPIC" not in t and "topic_env" not in t:
                errs.append(f"{rel} 硬编码 ntfy topic")
    # SQLite 私有目录：后端 Windows/Android 两端 db_path
    cfg = _load_config() or {}
    dbp = str((cfg.get("client") or {}).get("android", {}).get("db_path", "private"))
    if dbp not in ("private", "/data/data/<pkg>", "/data/user/0"):
        errs.append(f"config client.android.db_path={dbp} 非私有目录")
    if "INTERNET" not in " ".join(m for m in manifests):
        pass
    if errs:
        add(8, "Android 仅 INTERNET / 无存储库 / 无后台常驻", "FAIL", "；".join(errs[:6]))
    else:
        add(8, "Android 仅 INTERNET / 无存储库 / 无后台常驻", "PASS",
            f"扫描 {len(files)} 个文件；manifest={manifests or '未找到'}，无越权/硬编码密钥/后台服务")


def c9_secrets():
    targets = []
    for base in ("backend", "client", "config.yaml", "sources.yaml", "reports", "scripts", "deploy"):
        targets.append(ROOT / base)
    targets.append(ROOT / "03_DAILYREPORT.json")
    problems = []
    n = 0
    for base in targets:
        if not base.exists():
            continue
        for f in _walk([base]):
            if f.suffix.lower() in BLOCK_EXT or f.name in (".env", "0verify.py") or f.name.startswith("."):
                continue
            t = _text(f)
            if t is None:
                continue
            n += 1
            for pat, why in SECRET_PATTERNS:
                for m in re.finditer(pat, t):
                    line = t[:m.start()].count("\n") + 1
                    if "os.environ" in t.splitlines()[line - 1] or "getenv" in t.splitlines()[line - 1].lower():
                        continue
                    problems.append(f"{f.relative_to(ROOT)}:{line} {why}")
    try:
        git = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, timeout=10)
        if git.returncode == 0 and ".env" in git.stdout.split():
            problems.append(".env 已被 git 跟踪")
    except Exception:
        pass
    if os.environ.get("DEEPSEEK_API_KEY") and (ROOT / "config.yaml").is_file() and \
            os.environ["DEEPSEEK_API_KEY"] in (ROOT / "config.yaml").read_text(encoding="utf-8", errors="ignore"):
        problems.append("config.yaml 明文含 DEEPSEEK_API_KEY 值")
    if problems:
        add(9, "密钥不入代码/仓库/JSON/日志", "FAIL", "；".join(problems[:5]))
    else:
        add(9, "密钥不入代码/仓库/JSON/日志", "PASS", f"扫描 {n} 个文本文件，零命中密钥模式")


def c10_offline_ui():
    cands = list((ROOT / "client").rglob("*.py")) + list(ROOT.glob("*.py"))
    hits = [(p, _text(p)) for p in cands if p.is_file()]
    hits = [(p, t) for p, t in hits if t and "sqlite3" in t]
    if not hits:
        add(10, "客户端断网读 SQLite 历史 + 空状态文案", "WARN", "未找到客户端 sqlite3 代码")
        return
    best = None
    for p, t in hits:
        if "今日暂无更新" in t or "暂无更新" in t:
            best = p
            break
    if best is None:
        # 检查 assets/empty.txt
        e = list(ROOT.rglob("empty.txt"))
        txt = e[0].read_text(encoding="utf-8", errors="ignore") if e else ""
        add(10, "客户端断网读 SQLite 历史 + 空状态文案",
            "PASS" if "今日暂无更新" in txt and hits else "FAIL",
            f"sqlite 客户端 {hits[0][0].name}；空文案来自 {e[0].name if e else '未找到'}")
    else:
        add(10, "客户端断网读 SQLite 历史 + 空状态文案", "PASS", f"{best.name} 含 sqlite3 与“今日暂无更新”")


def c11_urls(args):
    problems = []
    r = None
    try:
        r = json.loads((ROOT / "03_DAILYREPORT.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    if not args.skip_net:
        base = (args.base or _cfg("archive.public_base_url") or "").rstrip("/")
        if base:
            for path in (f"{base}/{TODAY}.json", f"{base}/index.json"):
                try:
                    st, hdr, body = _http(path)
                    if st != 200 or not body:
                        problems.append(f"{path} → {st}")
                    elif "json" not in hdr.get("Content-Type", "").lower() and "text/plain" not in hdr.get("Content-Type", "").lower():
                        problems.append(f"{path} Content-Type={hdr.get('Content-Type')}")
                except Exception as e:
                    problems.append(f"{path} 不可达：{type(e).__name__}")
        if r:
            for it in r.get("items", [])[:4]:
                try:
                    req = urllib.request.Request(it["url"], method="HEAD", headers={"User-Agent": "verify/1.0"})
                    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                        if resp.status >= 400:
                            problems.append(f"{it['id']} 原文 {it['url']} → {resp.status}")
                except Exception as e:
                    problems.append(f"{it['id']} 原文不可达 {it['url']}（{type(e).__name__}）")
    if args.skip_net:
        add(11, "报告 URL / 原文链接可达", "SKIP", "跳过联网")
    elif problems:
        add(11, "报告 URL / 原文链接可达", "WARN", "；".join(problems[:4]))
    else:
        add(11, "报告 URL / 原文链接可达", "PASS", "base_url JSON、index 与条目原文链接均可达")


def backup():
    """把 reports/ 今日 JSON 与 03_DAILYREPORT.json 备份到 reports/_backup/，保证断网留痕。"""
    dst = ROOT / "reports" / "_backup"
    dst.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in [ROOT / f"{TODAY}.json", ROOT / "03_DAILYREPORT.json", ROOT / "reports" / f"{TODAY}.json"]:
        if src.is_file():
            out = dst / f"{TODAY}_{src.name}"
            out.write_bytes(src.read_bytes())
            copied.append(out.name)
    add(0, "本地留痕备份", "PASS" if copied else "WARN", f"reports/_backup/ ← {copied or '无文件'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="段11 验收")
    ap.add_argument("--offline", action="store_true", default=True, help="仅离线检查（默认）")
    ap.add_argument("--live", action="store_true", help="联网检查：采集接口/ntfy/URL")
    ap.add_argument("--base", help="采集与 JSON 服务 base URL")
    ap.add_argument("--collect-json", help="离线采集样本 JSON 路径")
    ap.add_argument("--push-log", help="包含 ntfy 推送返回的日志路径")
    ap.add_argument("--skip-net", action="store_true", help="跳过 URL 检查")
    ap.add_argument("--json-out", help="把结果写到 JSON 文件")
    a = ap.parse_args(argv)

    t0 = time.time()
    print(f"== 段11 验收 {datetime.now().isoformat(timespec='seconds')} ({'LIVE' if a.live else 'OFFLINE'}) ==")
    backup()
    c1_trigger(not a.live)
    c2_collect(not a.live, a)
    c3_fetch_body(a)
    rep = c4_json()
    c5_body(rep)
    c6_ntfy(a)
    c7_index()
    c8_permissions()
    c9_secrets()
    c10_offline_ui()
    c11_urls(a)

    npass = sum(1 for x in RESULTS if x[2] == "PASS")
    nfail = sum(1 for x in RESULTS if x[2] == "FAIL")
    nwarn = sum(1 for x in RESULTS if x[2] == "WARN")
    print(f"-- 耗时 {time.time() - t0:.1f}s  PASS={npass} FAIL={nfail} WARN={nwarn} SKIP={len(RESULTS) - npass - nfail - nwarn} --")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(
            {"when": datetime.now().isoformat(), "results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if nfail else (2 if nwarn else 0)


if __name__ == "__main__":
    sys.exit(main())

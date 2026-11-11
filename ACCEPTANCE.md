# 段11 验收 — 11 条清单 + 判断方法 + 修复建议

执行：`python verify.py`（离线） / `python verify.py --live`（联网） / `python verify.py --live --base https://你的域名/reports --collect-json sample.json --push-log logs/app.log`
结果码：`PASS` / `FAIL` / `WARN`（缺样本，需手动核对）/ `SKIP`。退出码 0 全过、1 有 FAIL、2 仅 WARN。

> 注意：`--live` 会连 ntfy 发一条“验收推送”到真实 topic（客户端会收到），并请求 `base_url` 的今天 JSON。不发真消息用 `--push-log` 指定已记录的推送日志。

---

## 一、11 条验收清单

| # | 验收项 | 判断方法（脚本如何判） | 通过标准 | 失败修复建议 |
|---|---|---|---|---|
| 1 | **18:01 触发采集** | 取 `logs/*.log` 最后一条带日期时间且含 `start/trigger/schedul/fire/run_daily/触发/执行` 的行；与今日 `18:01:00` 比较 | 同日、偏移 `0 ≤ Δ ≤ schedule.misfire_grace_seconds(300s)` | APScheduler 用 `CronTrigger(hour=18, minute=1, timezone="Asia/Shanghai")`；容器/主机时区设 `TZ=Asia/Shanghai`；systemd timer 检查 `OnCalendar=*-*-* 18:01:00 Asia/Shanghai` 且 `Persistent=true`；FunctionGraph 触发器时区同为 UTC+8 |
| 2 | **采集非空** | `--collect-json` 离线样本，或 `GET {base}/api/collect`，或调用 `backend.collect.get_collector(provider).collect(today)` | 列表长度 > 0 且每项含非空 `title`、`http(s) url` | 源站改版→更新 `sources.yaml` 选择器；加 UA/超时重试；先跑 `provider: fixture` 确认管线通；条目过滤条件（日期=今天、正文长度≥N）过严 |
| 3 | **正文抓取** | 取样本条目 `raw_text`；统计汉字数、与标题汉字数比较、正则计 `<img>`/markdown 图/`data:image/` | 汉字数 > max(50, 标题汉字×2) 且图片数 = 0 | 选择器抓到了导航/列表页→定位正文容器（如 `article .pages_content`）；列表页先取详情页 URL 再抓详情；去图片 `<img>`、`style` 背景图；解码按 `encoding: auto` 探测 |
| 4 | **JSON 解析/契约** | 解析 `03_DAILYREPORT.json`（或 `--json report.json`）：UTF-8、字段、日期格式、六类、计数、id 序号、URL、无 base64 | 3.3 节全部约束成立且无 `image/base64` | 缺类→`category_summary` 固定写六项（缺补 0）；计数不符→从 items 重算再写；`id` 用 `f"{date}-{i+1:02d}"`；`summary` 截断而不是留空；`ensure_ascii=False` 写 UTF-8 |
| 5 | **推送正文 1000~1200 字 / ≤4096B** | 取日志中 publish/body 行或报告 `summary_markdown`，计汉字数与 UTF-8 字节 | `1000 ≤ 汉字 ≤ 1200` 且字节 ≤ 4096 且正文含 JSON 地址 | 先拼 `json_url` 行再按字节预算截断正文（`body.encode()[:4090-len(url)]` 后 utf-8 安全回退）；正文以 `summary_markdown` 为主来源，别拼 items 全文；超 4096 先砍“背景解读”段 |
| 6 | **ntfy 推送返回 id/200** | `--live` 真发一条；或 `--push-log` 从日志提取 `"id"` 与 `"status"` | HTTP 200 且返回 JSON 含非空 `id` | 401/403 → `NTFY_TOKEN` 未注入或 topic 未绑定 token；404 → topic 太短/不存在；413 → 正文超 `max_payload_bytes`；超时 → `Priority: low` + 客户端 `X-Message-*` 不宜过长；返回 `id` 要落日志（不落 token） |
| 7 | **reports/ + index.json 落盘** | 读 `reports/index.json`：`dates` 非空倒序去重、`updated_at` ISO、每个 date 有同名 JSON | 全部满足 | 归档后必须更新 index（先追加 date 再写 `updated_at`）；用 `atomic_write`：写 `.tmp` → `os.replace`；远程归档后再写本地 index（见 `remote_archiver.py`） |
| 8 | **Android 仅 INTERNET / 私有库 / 非常驻** | 扫 `AndroidManifest.xml` 权限、gradle 依赖（appcompat/workmanager/firebase/推送 SDK）、`externalFilesDir` 等、`config.yaml` 的 `db_path=private` | 仅 `android.permission.INTERNET`；无三方推送/WorkManager；SQLite 在私有目录；无 exported service | 删多余 `<uses-permission>`；开工单删 WorkManager，改 `MainActivity.onStart` 触发一次性同步；推送用 ntfy 轮询/`Ntfy` 系统通知由 OS 投递，不内置 SDK；`getDatabasePath()` 走私有目录 |
| 9 | **密钥不入代码/仓库/JSON/日志** | 扫 `backend/ client/ config.yaml sources.yaml reports/ 03_DAILYREPORT.json`，正则 `sk-…`、`Bearer …`、`DEEPSEEK_API_KEY=`、`NTFY_TOKEN=`，另查 git 是否跟踪 `.env` | 零命中 | 全部改 `os.environ[…]`（Key/Token），`.env` 加进 `.gitignore`；`config.yaml` 只留 `*_env` 字段；日志脱敏：写日志前把 token 替换为 `***`；已提交则轮换 Key 并 `git filter-repo` 清史 |
| 10 | **客户端断网读 SQLite 历史 + 空状态文案** | 在 `client/**/*.py`（或 Win/Android 壳）找 `sqlite3` 且含 `今日暂无更新`（或 `assets/empty.txt`） | 有 sqlite3 读路径 + 空文案命中 | 同步失败 `except` 分支必须回读 SQLite 并渲染（不能清空列表）；断网且有历史→顶部灰条“网络不可用，显示本地缓存”；无历史→“今日暂无更新”居中 `#9CA3AF` |
| 11 | **报告 URL / 原文链接可达** | `--live`：`GET {base}/{today}.json`、`GET {base}/index.json`；`HEAD` 前 4 条原文 URL | 全部 200，Content-Type 为 JSON/纯文本 | 静态托管目录指向 `reports/`；Nginx `default_type application/json` 或 `types { application/json json; }`；原文 403 → 加 UA、改 `https://`、避开反爬（只做 HEAD/GET 首页）；链接为相对路径时补 `public_base_url` |

---

## 二、3 条手动核对（脚本不能替代）

1. **真机收推送 + 页面观感**：18:05 后看 Android 通知栏是否收到“民生政策日报 YYYY-MM-DD”，点开后顶部日期、横向分类（空分类不出现）、条目 16/600 标题 + 14/400 摘要 3 行省略、来源·日期 12/400；Windows 三栏是否 200px/120px/自适应、1100×720、无动画无阴影。**判定**：空分类消失、无数据日显示“今日暂无更新”、配色 `#1A56DB/#1F2933/#6B7280/#E5E5E5`。
2. **断网离线翻阅**：开飞行模式杀进程重启客户端。**判定**：已有历史日期可点开看到旧条目；缺失日期不出现在列表；首次无历史时显示“今日暂无更新”；有历史时顶部出现细条“网络不可用，显示本地缓存”；APK 设置页里权限只有“网络”一项。
3. **Key/token 全链路不入库**：① `grep -R "sk-" .` 与 `git log -p \| grep -i "bearer\|NTFY_TOKEN=\|DEEPSEEK_API_KEY="` 均无输出（含历史提交）；② 打开客户端 APK/ZIP 解包 ctrl+F `sk-`、`ntfy`、topic 字符串无命中；③ 已推送的 ntfy 消息正文只含摘要 + JSON 地址，不含任何 Key/token；④ 服务器 `env \| grep -i deepseek` 只在该账号的加密环境变量中存在。**判定**：四项全部为空/不存在。

---

## 三、执行顺序建议

```bash
python verify.py                                   # 先跑离线，看 4/5/8/9 是否硬失败
python verify.py --live --base https://example.com/reports   # 18:05 后跑，校 1/2/3/6/11
python verify.py --push-log logs/app.log --collect-json sample.json   # 不想真发消息时
```

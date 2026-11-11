---
title: PolicyDaily 约束参数清单
aliases:
  - 民生政策日报 约束清单
  - PolicyDaily Constraints
tags:
  - project/PolicyDaily
  - type/spec
  - layer/obsidian
version: 1.0
source:
  - 00_CONSTRAINTS.md
  - 01_INTERFACES.md
  - SECURITY_CHECKLIST.md
  - ACCEPTANCE.md
status: active
updated: 2026-11-11
---

# PolicyDaily 约束参数清单

> 目标：每天 18:01 采集中央民生政策文字 → DeepSeek Flash 精炼成分分类日报 → 18:05 经 ntfy 推送「正文 + JSON 地址」→ Android APK / Windows exe 拉取 JSON 存 SQLite，可离线翻阅。

数据流单向：`schedule → collect → process → archive → publish → client`，跨层只走函数签名与 JSON 契约。

---

## 一、硬约束（H1–H6）

| 编号 | 约束 | 落点 |
|---|---|---|
| H1 | 18:01 触发采集、18:05 发送，位于 DeepSeek 空闲时段 | `schedule.daily_at` / `publish_at` |
| H2 | 只处理文字；不下载图片、不做 OCR；JSON 不含图片 / base64 | `collector.text_only / skip_images` |
| H3 | Android 仅 `INTERNET` 权限，不申请相机/相册/麦克风/定位/通讯录/短信/电话/蓝牙/NFC/存储；SQLite 放私有目录 | `AndroidManifest.xml`、`client.android.*` |
| H4 | 客户端只做：收推送或启动同步 → 拉 JSON → 存 SQLite → 展示；不爬取、不调模型、无后台常驻 | `SyncWorker` / `sync.py` |
| H5 | 每天写 `reports/YYYY-MM-DD.json` + `index.json`；客户端补拉缺失日期，断网读已存历史 | `archive`、`client.sync.fill_missing_dates` |
| H6 | 模块可拆卸，只通过接口通信，换实现只改配置 | `config.yaml` provider 字段 |

## 二、安全约束（S1–S5）

| 编号 | 约束 | 落点 |
|---|---|---|
| S1 | DeepSeek Key 只存后端加密环境变量 `DEEPSEEK_API_KEY`，不进客户端/JSON/日志/Git | `processor.deepseek.api_key_env` |
| S2 | ntfy 用长随机 topic（≥32 字符）+ Bearer Token，token 不进仓库/日志 | `NTFY_TOPIC` / `NTFY_TOKEN` |
| S3 | 客户端不硬编码任何 Key/token；客户端配置只有 `base_url` + `refresh_url` | `client.*` |
| S4 | FunctionGraph 委托权限留空，不配 APIG | `schedule.functiongraph.delegation_permissions: []` |
| S5 | JSON 只含摘要和原文链接，不含敏感信息 | `model.py`、`reports/*.json` |

---

## 三、按模块划分的约束

### ① schedule 调度

- 触发时间 `18:01`（Asia/Shanghai），发送时间 `18:05`。
- 迟到容差 `misfire_grace_seconds = 300`（验收项 1：`0 ≤ Δ ≤ 300s`）。
- 默认 `cron`（APScheduler `CronTrigger(hour=18, minute=1, timezone="Asia/Shanghai")`）；可替换 `functiongraph`。
- S4：函数委托权限 `[]`，不开放公网 HTTP 入口（无 APIG、无函数 URL）。
- FunctionGraph 触发器时区同为 UTC+8；执行角色最小权限：读采集出口 + 写 `reports/` + 发 ntfy。

### ② collect 采集

- 输入 `date: 'YYYY-MM-DD'`；输出 `[{'title','url','source','raw_text'}]`。
- H2：`text_only: true`、`skip_images: true`，不下载图片、不做 OCR。
- 失败项跳过，不抛到上层。
- 每源上限 `max_items_per_source = 30`；超时 `15s`；重试 `2` 次。
- 验收项 2：列表长度 > 0 且每项 `title` 非空、`url` 为 `http(s)`。
- 验收项 3：`raw_text` 汉字数 > max(50, 标题汉字×2) 且图片数 = 0。
- 源站改版只改 `sources.yaml` 选择器；离线调试用 `provider: fixture`。

### ③ process 处理

- 默认 `deepseek`（DeepSeek V4.1 Flash，OpenAI 兼容），可替换 `rule`（关键词规则兜底/降级）。
- 分类枚举固定六类：`民生 / 经济金融 / 科技产业 / 安全国防 / 政务法治 / 其他`。
- `items[i].category` 必须属于上述六类之一。
- 单日处理上限 `max_items = 20`。
- S1：Key 只从 `os.environ["DEEPSEEK_API_KEY"]` 读取，日志脱敏，错误信息只写状态码。

### ④ archive 归档

- 写 `reports/{date}.json` + 更新 `index.json`；返回 JSON 公开 URL。
- `atomic_write: true`：先写 `.tmp` 再 `os.replace`。
- `index.json`：`dates` 非空、倒序、去重；`updated_at` 为 ISO 时间；每个 date 均有同名 JSON。
- `keep_days = 0` 表示永久保留。
- `public_base_url` 为推送中 JSON 地址前缀。
- S5：`.tmp`、`.git`、配置文件、日志禁止被匿名访问（匿名列表一律 403）。

### ⑤ publish 推送

- 正文截断 1000–1200 中文字；整条 UTF-8 ≤ 4096 字节；正文含 JSON 地址。
- 标题模板 `民生政策日报 {date}`。
- S2：`Authorization: Bearer`，topic/token 只从环境变量读取。
- 推送正文只含标题/摘要/JSON URL，无 Key、无 token、无内部地址。
- 推送失败不影响已归档数据；返回的 `id` 落日志（不落 token）。

### ⑥ client 客户端

- 只做：收推送或启动同步 → 拉 JSON → 存 SQLite → 展示（H4）。
- Android：仅 `<uses-permission android:name="android.permission.INTERNET"/>`；无三方推送 SDK、无 WorkManager；SQLite 在私有目录；无 exported service、无后台常驻。
- Windows：`sqlite3` 存 `%APPDATA%/PolicyDaily/reports.db`。
- 只读：仅两个 GET，无鉴权头、无上传接口、无模型调用。
- S3：源码全局搜索 `DEEPSEEK`、`sk-`、`Bearer`、`NTFY_TOKEN` 结果为空。
- H5：用 `index.json` 的 `dates` 比对本地 SQLite，补拉缺失日期。
- 同步失败必须回读 SQLite 渲染，不清空列表。

---

## 四、参数表

### 4.1 schedule

| 参数 | 值 | 说明 |
|---|---|---|
| `provider` | `cron` | 可替换 `functiongraph` |
| `timezone` | `Asia/Shanghai` | |
| `daily_at` | `18:01` | H1 |
| `publish_at` | `18:05` | H1 |
| `misfire_grace_seconds` | `300` | 迟到容忍 |

### 4.2 collector

| 参数 | 值 | 说明 |
|---|---|---|
| `provider` | `html` | 可替换 `rss` / `fixture` |
| `timeout_seconds` | `15` | |
| `retries` | `2` | |
| `user_agent` | `Mozilla/5.0 (compatible; policy-daily/1.0)` | |
| `max_items_per_source` | `30` | |
| `text_only` | `true` | H2 |
| `skip_images` | `true` | H2 |
| `sources_file` | `backend/sources.yaml` | 唯一源清单 |
| `html.encoding` | `auto` | |
| `rss.feed_limit` | `50` | |

### 4.3 processor

| 参数 | 值 | 说明 |
|---|---|---|
| `provider` | `deepseek` | 可替换 `rule` |
| `categories` | 民生、经济金融、科技产业、安全国防、政务法治、其他 | 固定枚举 |
| `max_items` | `20` | 单日上限 |
| `model` | `deepseek-v4.1-flash` | |
| `base_url` | `https://api.deepseek.com/v1` | OpenAI 兼容 |
| `api_key_env` | `DEEPSEEK_API_KEY` | S1 |
| `temperature` | `0.2` | |
| `max_tokens` | `4096` | |
| `timeout_seconds` | `60` | |
| `retries` | `1` | |

### 4.4 archive

| 参数 | 值 | 说明 |
|---|---|---|
| `provider` | `local` | 可替换 `remote`（OBS/S3） |
| `dir` | `reports/` | |
| `write_index` | `true` | H5 |
| `atomic_write` | `true` | `.tmp` → rename |
| `keep_days` | `0` | 永久保留 |
| `public_base_url` | `https://example.com/reports` | 推送 URL 前缀 |
| `remote.access_key_env` | `OBS_AK` | 仅环境变量 |
| `remote.secret_key_env` | `OBS_SK` | 仅环境变量 |

### 4.5 publisher（ntfy）

| 参数 | 值 | 说明 |
|---|---|---|
| `provider` | `ntfy` | 可替换 `noop` |
| `server` | `https://ntfy.sh` | 可替换自建 |
| `topic_env` | `NTFY_TOPIC` | S2，≥32 字符随机 |
| `token_env` | `NTFY_TOKEN` | S2 |
| `auth_scheme` | `Bearer` | |
| `title_template` | `民生政策日报 {date}` | |
| `body_char_min` | `1000` | 正文下限（中文字） |
| `body_char_max` | `1200` | 正文上限（中文字） |
| `max_payload_bytes` | `4096` | UTF-8 |
| `priority` | `default` | 超时场景可降 `low` |
| `tags` | `[memo]` | |
| `include_json_url` | `true` | 正文末尾附 JSON 地址 |

### 4.6 client

| 参数 | 值 | 说明 |
|---|---|---|
| `base_url` | `https://example.com/reports` | 只读 JSON 源 |
| `refresh_url_template` | `https://ntfy.sh/{topic}` | 订阅地址由用户手动填 |
| `android.permissions` | `[INTERNET]` | H3，仅此一项 |
| `request_camera/storage/location/contacts/microphone/sms/phone/bluetooth/nfc` | 全 `false` | H3 |
| `android.db_path` | `private` | H3 |
| `android.background_service` | `false` | H4 |
| `windows.window_title` | `民生政策日报` | |
| `windows.width × height` | `1100 × 720` | 最小 800 × 500 |
| `windows.pane_date_px` | `200` | 日期栏固定 |
| `windows.pane_category_px` | `120` | 分类栏固定 |
| `windows.pane_items` | `auto` | flex:1 |
| `windows.db_path` | `%APPDATA%/PolicyDaily/reports.db` | |
| `sync.fetch_timeout_seconds` | `15` | |
| `sync.fetch_retries` | `2` | |
| `sync.fill_missing_dates` | `true` | H5 |
| `sync.empty_text` | `今日暂无更新` | |

### 4.7 JSON 契约字段

| 对象 | 字段 | 规则 |
|---|---|---|
| PolicyItem | `id` | `{date}-{序号:02d}`，如 `2026-11-11-01` |
| PolicyItem | `category` | 六类之一 |
| PolicyItem | `title` | 非空 |
| PolicyItem | `summary` | 非空，截断而非留空 |
| PolicyItem | `source` | 发布机构 |
| PolicyItem | `url` | `http(s)` 原文链接 |
| PolicyItem | `publish_date` | `YYYY-MM-DD` |
| DailyReport | `date` | `YYYY-MM-DD` |
| DailyReport | `generated_at` | ISO8601 带 +08:00 |
| DailyReport | `item_count` | `== len(items)` |
| DailyReport | `category_summary` | 固定六项，缺失写 `count: 0` |
| DailyReport | `items` | PolicyItem 数组 |
| index.json | `updated_at` | ISO8601 |
| index.json | `dates` | 倒序、去重、非空 |

**契约断言（`tests/test_contract.py`）**

- `category_summary` 必含全部 6 类，缺失写 `count: 0`。
- `sum(count) == item_count == len(items)`。
- `items[i].category` 与 `category_summary` 逐类一致。
- 空日：`item_count: 0`、`items: []`、`category_summary` 六项全 0。
- JSON 不含 Key/token（S1/S2/S5）；`ensure_ascii=False` 写 UTF-8。

### 4.8 UI 规范

| 项 | 值 |
|---|---|
| 主色 | `#1A56DB`（标题栏/选中态/链接） |
| 正文色 | `#1F2933` |
| 辅助色 | `#6B7280`（日期/来源/次要说明） |
| 背景 | `#FFFFFF` |
| 分隔线 | `#E5E5E5`，1px |
| 空状态文字 | `#9CA3AF` |
| 一级字号 | 16px / 24px / 600（页面标题、条目标题） |
| 二级字号 | 14px / 22px / 400（摘要、列表项，最多 3 行省略） |
| 三级字号 | 12px / 18px / 400（日期、来源、分类标签） |
| Windows 三栏 | 日期 200px / 分类 120px / 条目 flex:1 |
| Windows 窗口 | 1100×720，最小 800×500 |
| 选中态 | `#1A56DB` 10% 透明度，无阴影无过渡 |
| Android 顶部日期 | 固定 56dp，点击弹日期列表 |
| Android 横向分类 | 固定 44dp，可横滑，空分类隐藏 |
| 通用 | 无动画、无阴影、无圆角装饰、无渐变；空分类隐藏 |
| 空状态文案 | 今日暂无更新（12/400，`#9CA3AF`，居中） |
| 断网提示 | 网络不可用，显示本地缓存（12/400，`#6B7280` 细条） |

---

## 五、Checklist

### 5.1 安全（任一 ❌ 不得上线）

**S1 DeepSeek Key**

- [ ] Key 只放后端加密环境变量 `DEEPSEEK_API_KEY`，代码只读 `os.environ`
- [ ] 不写入 `config.yaml`、不写 `.env` 明文、不进 Git（`.gitignore` 含 `.env`）
- [ ] 日志脱敏：打印/异常栈不出现 Key，错误信息只写状态码
- [ ] 平台泄漏响应不回显请求头；Key 轮换后旧 Key 立即吊销
- [ ] 磁盘加密 / 密钥托管（KMS、FunctionGraph 加密变量）开启

**S2 ntfy Token**

- [ ] Topic 长随机（≥32 字符不可猜），只存 `NTFY_TOPIC`
- [ ] 发送 Token 只存 `NTFY_TOKEN`，用 `Authorization: Bearer`
- [ ] 自建 ntfy 或付费 ACL：topic 写权限受限，读订阅单独凭据
- [ ] 客户端订阅地址由用户手动填入，运行时不落盘 token
- [ ] 推送正文只含标题/摘要/JSON URL，无 Key、无 token、无内部地址

**S4 FunctionGraph**

- [ ] `delegation_permissions: []`，不绑定 APIG / API 网关
- [ ] 执行角色最小权限：读采集出口 + 写 `reports/` + 发 ntfy，不含 OBS 管理权限
- [ ] 环境变量用「加密变量」，禁止代码常量硬编码
- [ ] 出站白名单：仅采集源域名、`api.deepseek.com`、ntfy 服务器
- [ ] 定时触发器 18:01（UTC+8），无公网 HTTP 入口

**S3 / H3 客户端**

- [ ] 全局搜索 `DEEPSEEK`、`sk-`、`Bearer`、`NTFY_TOKEN` 结果为空
- [ ] 客户端配置只有 `base_url` + `refresh_url`
- [ ] `AndroidManifest.xml` 只声明 `INTERNET`，`aapt dump permissions` 复核
- [ ] SQLite 只落私有目录；APK/exe 打包后再扫一次 strings
- [ ] 客户端只读：无上传接口、无模型调用、无后台上报

**S5 静态托管**

- [ ] 对象存储关闭 ListBucket / 目录列表（匿名禁止 `s3:ListBucket`）
- [ ] 只放行 `GET {base}/{YYYY-MM-DD}.json` 与 `GET {base}/index.json`，其余 403
- [ ] `.tmp`、`.git`、配置文件、日志禁止匿名访问
- [ ] CORS 仅允许客户端需要的源，或干脆不开放
- [ ] JSON 自检：仅 8 字段，无图片/base64/Key/token/内部 URL

**上线前一次性**

- [ ] `git grep -iE "sk-|Bearer [A-Za-z0-9]{10,}|NTFY_TOKEN\s*="` 无命中
- [ ] 匿名访问 `{base}/`、`{base}/reports/` 均 403
- [ ] 抓一次真实推送载荷：无 Key/token，UTF-8 ≤ 4096 字节
- [ ] 关闭调试日志，`DEBUG=false`，异常栈不外露

### 5.2 验收 11 项（`python verify.py`）

| # | 验收项 | 通过标准 |
|---|---|---|
| 1 | 18:01 触发采集 | 同日偏移 `0 ≤ Δ ≤ 300s` |
| 2 | 采集非空 | 长度 > 0，`title` 非空，`url` 为 `http(s)` |
| 3 | 正文抓取 | 汉字数 > max(50, 标题汉字×2) 且图片数 = 0 |
| 4 | JSON 解析/契约 | §3.3 全部约束成立且无 `image/base64` |
| 5 | 推送正文 | `1000 ≤ 汉字 ≤ 1200`、字节 ≤ 4096、含 JSON 地址 |
| 6 | ntfy 返回 | HTTP 200 且 JSON 含非空 `id` |
| 7 | 落盘 + index | `dates` 非空倒序去重、`updated_at` ISO、每日有同名 JSON |
| 8 | Android 权限 | 仅 `INTERNET`、无三方推送 SDK/WorkManager、SQLite 私有、无 exported service |
| 9 | 密钥不入库 | `sk-`/`Bearer`/`DEEPSEEK_API_KEY=`/`NTFY_TOKEN=` 零命中，`.env` 未被跟踪 |
| 10 | 断网历史 + 空文案 | 有 sqlite3 读路径 + `今日暂无更新` 命中 |
| 11 | URL 可达 | 两个 GET 与前 4 条原文 URL 均 200，Content-Type 为 JSON/纯文本 |

- [ ] 结果码 `PASS` / `FAIL` / `WARN` / `SKIP` 判定；退出码 0 全过、1 有 FAIL、2 仅 WARN
- [ ] 先离线：`python verify.py`（看 4/5/8/9 是否硬失败）
- [ ] 再联网：`python verify.py --live --base https://example.com/reports`（18:05 后）
- [ ] 不想真发消息：`python verify.py --push-log logs/app.log --collect-json sample.json`

### 5.3 手动核对（脚本不可替代）

- [ ] 真机收推送 + 页面观感：通知标题为「民生政策日报 YYYY-MM-DD」；空分类消失；无数据日显示「今日暂无更新」；配色 `#1A56DB/#1F2933/#6B7280/#E5E5E5`；Windows 三栏 200px/120px/自适应、1100×720、无动画无阴影
- [ ] 断网离线翻阅：飞行模式杀进程重启，历史日期可点开；有历史出现「网络不可用，显示本地缓存」；APK 权限仅「网络」
- [ ] Key/token 全链路不入库：`grep -R "sk-" .`、`git log -p | grep -i "bearer\|NTFY_TOKEN=\|DEEPSEEK_API_KEY="`、APK 解包搜索、已推送正文、服务器 `env | grep -i deepseek` 全部为空/无问题

### 5.4 模块可拆卸（H6）

- [ ] 每个 provider 有 `get_xxx(name)` 工厂 + `base.py` 抽象（签名固定）
- [ ] 换实现只改 `config.yaml` 的 `provider` 字段，不改代码
- [ ] 层间不 import 对方内部模块，跨层只走函数签名与 JSON 契约
- [ ] `app.py` 不写业务，只按 `schedule → collect → process → archive → publish` 编排
- [ ] `config.py`（env 注入）、`model.py`（to_dict/from_dict）、`base.py`、`AndroidManifest.xml`、`schema.sql`、`api.md`、`test_contract.py` 均标记不可替换

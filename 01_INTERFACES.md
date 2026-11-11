# 01_INTERFACES — 段1 设计层

> 本文件只定契约。实现见后续段落。换实现只改 `config.yaml`。

---

## 1. 文字架构图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              config.yaml                                 │
│         （唯一装配点：每层选默认实现或替换实现，只改 provider 字段）        │
└───────────────┬──────────────┬──────────────┬──────────────┬─────────────┘
                │              │              │              │
        ┌───────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐
   ①    │  schedule    │ │  collect   │ │  process   │ │  archive   │
   调度  │  18:01 触发   │→│  采集文字   │→│ 分类+摘要   │→│ 落盘 JSON   │
        │              │ │            │ │            │ │            │
        │ 默认:        │ │ 默认:      │ │ 默认:      │ │ 默认:      │
        │ cron/APSched │ │ requests+  │ │ DeepSeek   │ │ 本地磁盘   │
        │ 可替换:      │ │ BeautifulS │ │ V4.1 Flash │ │ reports/   │
        │ FunctionGr.  │ │ oup 抓HTML │ │ (OpenAI兼  │ │ 可替换:    │
        │ 定时器       │ │ 可替换:    │ │ 容)        │ │ OBS/S3     │
        │              │ │ RSS/API    │ │ 可替换:    │ │            │
        │              │ │ /静态样例  │ │ 规则分类   │ │            │
        └───────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘
                │              │              │              │
                └──────┬───────┴──────┬───────┴──────┬───────┘
                       │              │              │
                       ▼              ▼              ▼
              ┌─────────────────────────────────────────────┐
              │              ⑤ publish                       │
              │   18:05 发 ntfy：正文(≤4096B,1000~1200字)    │
              │   + JSON 地址                                │
              │  默认: ntfy.sh + 长随机 topic + Bearer Token │
              │  可替换: 自建 ntfy / 其他推送（同签名）       │
              └──────────────────┬──────────────────────────┘
                                 │  ntfy 推送（不含任何 Key/token）
                                 ▼
              ┌─────────────────────────────────────────────┐
              │            ⑥ client（两个壳）                │
              │  Android(仅INTERNET, 顶部日期+横向分类+列表) │
              │  Windows(三栏: 日期200px/分类120px/条目)     │
              │  只做：收推送或启动同步→拉JSON→存SQLite→展示 │
              │  不爬取、不调模型、无后台常驻                 │
              │  默认: 直连 GET {base}/{date}.json           │
              │  可替换: 换 base URL / 换 mirrors 列表       │
              └─────────────────────────────────────────────┘
```

数据流单向：`schedule → collect → process → archive → publish → client`。
跨层只走第 3 节的函数签名与 JSON 契约，层间不 import 对方内部模块。

---

## 2. 目录结构

```
backend/
  app.py                [可替换]  入口/装配，读 config 调四层，不写业务
  config.py             [不可替换] 读 config.yaml → 数据类，env 注入密钥
  model.py              [不可替换] PolicyItem / DailyReport 数据类 + to_dict/from_dict
  collect/
    __init__.py         [不可替换] get_collector(name) 工厂
    base.py             [不可替换] Collector 抽象（签名固定）
    html_collector.py   [可替换] requests + BeautifulSoup（默认）
    rss_collector.py    [可替换] RSS/Atom
    fixture_collector.py[可替换] 本地样例（测试/离线用）
  process/
    __init__.py         [可替换] get_processor(name) 工厂
    base.py             [不可替换] Processor 抽象（签名固定）
    deepseek_processor.py[可替换] DeepSeek V4.1 Flash（默认）
    rule_processor.py   [可替换] 关键词规则分类（兜底/降级）
  archive/
    __init__.py         [可替换] get_archiver(name) 工厂
    base.py             [不可替换] Archiver 抽象（签名固定）
    local_archiver.py   [可替换] 写 reports/（默认）
    remote_archiver.py  [可替换] 上传 OBS/S3 后回本地写 index
  publish/
    __init__.py         [可替换] get_publisher(name) 工厂
    base.py             [不可替换] Publisher 抽象（签名固定）
    ntfy_publisher.py   [可替换] ntfy + Bearer（默认）
    noop_publisher.py   [可替换] 不发（调试）
  schedule/
    __init__.py         [可替换] get_scheduler(name) 工厂
    base.py             [不可替换] Scheduler 抽象
    cron_scheduler.py   [可替换] APScheduler 18:01（默认）
    fg_handler.py       [可替换] 华为 FunctionGraph 定时器入口（委托权限留空）
  sources.yaml          [可替换] 采集源清单（URL/选择器/解码）
  prompts/
    classify.txt        [可替换] 分类 prompt 模板
    summarize.txt       [可替换] 摘要 prompt 模板
  tests/
    test_contract.py    [不可替换] 校验 JSON 契约与 category_summary 合计

client/
  shared/
    api.md              [不可替换] 客户端可用的两个 HTTP 契约（GET json, GET index）
    schema.sql          [不可替换] SQLite 建表（私有目录）
  android/
    AndroidManifest.xml [不可替换] 仅 <uses-permission INTERNET>
    app/build.gradle    [可替换] 无三方推送 SDK；轻量 HTTP(S)
    .../MainActivity.kt [可替换] 顶部日期 + 横向分类 + 条目列表
    .../SyncWorker.kt   [可替换] 启动同步/收推送后同步，非常驻
    .../DbHelper.kt     [可替换] SQLite 私有目录读写
  windows/
    app.py              [可替换] tkinter/PySide，三栏
    sync.py             [可替换] requests 拉 JSON
    db.py               [可替换] sqlite3 读写
    build.spec          [可替换] PyInstaller 打包
  assets/
    empty.txt           [可替换] 空状态文案（与 UI 规范一致）

reports/
  YYYY-MM-DD.json       [可替换] 每日日报（结构固定）
  index.json            [可替换] 日期索引 {"dates":[...], "updated_at":"..."}
```

不可替换 = 契约本身（签名、字段、权限、表结构）；可替换 = 仅内部实现，换时只改 config。

---

## 3. 接口定义

### 3.1 函数签名（Python，仅签名，不写实现）

```python
# collect
from typing import Protocol
class Collector(Protocol):
    def collect(self, date: str) -> list[dict]:
        """date: 'YYYY-MM-DD'；返回原始条目 [{'title','url','source','raw_text'}]。
        只取文字：不下载图片、不做 OCR。失败项跳过，不抛到上层。"""
        ...

# process
class Processor(Protocol):
    def process(self, date: str, raw_items: list[dict]) -> "DailyReport":
        """调用模型分类+摘要。category 必须属于六类之一。"""
        ...

# archive
class Archiver(Protocol):
    def archive(self, report: "DailyReport") -> str:
        """写 reports/{date}.json + 更新 index.json；返回 JSON 的公开 URL。"""
        ...

# publish
class Publisher(Protocol):
    def publish(self, report: "DailyReport", json_url: str) -> bool:
        """发送 ntfy：正文截断 1000~1200 中文字，总 UTF-8 ≤ 4096 字节，含 json_url。
        返回是否发送成功（失败不影响已归档数据）。"""
        ...
```

```python
# app.py 装配（签名）
def run_daily(config_path: str = "config.yaml", date: str | None = None) -> int:
    """1 行编排：s = cfg.schedule → collect → process → archive → publish。"""
```

### 3.2 PolicyItem JSON 示例

```json
{
  "id": "2026-11-11-01",
  "category": "民生",
  "title": "关于优化基本医疗保险门诊共济保障机制的指导意见",
  "summary": "国务院办公厅发文，要求2027年底前细化门诊共济保障范围，扩大普通门诊统筹覆盖，减轻参保人员门诊费用负担。",
  "source": "中国政府网",
  "url": "https://www.gov.cn/zhengce/content/202611/content_000001.htm",
  "publish_date": "2026-11-11"
}
```

字段固定为 8 个：`id / category / title / summary / source / url / publish_date`。
无图片字段、无 base64、无敏感信息（S5）。

### 3.3 DailyReport JSON 示例

```json
{
  "date": "2026-11-11",
  "generated_at": "2026-11-11T18:04:12+08:00",
  "item_count": 4,
  "category_summary": [
    {"category": "民生", "count": 2},
    {"category": "经济金融", "count": 1},
    {"category": "科技产业", "count": 0},
    {"category": "安全国防", "count": 0},
    {"category": "政务法治", "count": 1},
    {"category": "其他", "count": 0}
  ],
  "items": [
    {
      "id": "2026-11-11-01",
      "category": "民生",
      "title": "关于优化基本医疗保险门诊共济保障机制的指导意见",
      "summary": "国务院办公厅发文，要求2027年底前细化门诊共济保障范围，扩大普通门诊统筹覆盖。",
      "source": "中国政府网",
      "url": "https://www.gov.cn/zhengce/content/202611/content_000001.htm",
      "publish_date": "2026-11-11"
    },
    {
      "id": "2026-11-11-02",
      "category": "民生",
      "title": "关于推进普惠托育服务体系建设的通知",
      "summary": "国家发改委等部门提出，2027年前新增普惠托位若干，支持社区嵌入式托育。",
      "source": "国家发改委",
      "url": "https://www.ndrc.gov.cn/xwdt/202611/notice_000002.htm",
      "publish_date": "2026-11-11"
    },
    {
      "id": "2026-11-11-03",
      "category": "经济金融",
      "title": "关于进一步支持小微企业融资的若干措施",
      "summary": "人民银行、金融监管总局发文，扩大普惠小微贷款支持工具范围，下调部分再贷款利率。",
      "source": "中国人民银行",
      "url": "https://www.pbc.gov.cn/goutongjiaoliu/202611/m_000003.htm",
      "publish_date": "2026-11-11"
    },
    {
      "id": "2026-11-11-04",
      "category": "政务法治",
      "title": "关于规范行政许可事项清单管理的通知",
      "summary": "要求各地2026年底前完成行政许可事项清单动态调整，清单外不得实施许可。",
      "source": "司法部",
      "url": "https://www.moj.gov.cn/pub/sfbgw/202611/n_000004.htm",
      "publish_date": "2026-11-11"
    }
  ]
}
```

**约束（由 `tests/test_contract.py` 断言）：**

- `category_summary` 必含全部 6 个分类枚举，缺失写 `count: 0`。
- `sum(count) == item_count == len(items)`。上例：2+1+0+0+1+0 = 4 = 4。✔
- `items[i].category` 与 `category_summary` 计数逐类一致。
- 空日：`item_count: 0`，`items: []`，`category_summary` 仍六项全 0。
- JSON 不含 Key/token（S1/S2/S5）。

### 3.4 index.json 示例

```json
{
  "updated_at": "2026-11-11T18:04:12+08:00",
  "dates": ["2026-11-11", "2026-11-10", "2026-11-09"]
}
```

客户端用 `dates` 比对本地 SQLite 已有日期，补拉缺失（H5）。

### 3.5 客户端 HTTP 契约（client/shared/api.md）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `{base_url}/{YYYY-MM-DD}.json` | DailyReport |
| GET | `{base_url}/index.json` | 日期索引 |

无鉴权头、无 Key、无上传接口。客户端只读（H4）。client 全部配置只有 `base_url` + `refresh_url`（拉起同步用），不含 DeepSeek Key（S1/S3）。

---

## 4. config.yaml 完整示例

```yaml
# ============ 调度 ============
schedule:
  provider: cron                    # 默认 cron | 可替换: functiongraph
  timezone: Asia/Shanghai
  daily_at: "18:01"                 # H1
  publish_at: "18:05"               # H1（archive 完成后触发 publish）
  misfire_grace_seconds: 300        # 迟到容忍
  cron:
    trigger: cron
  functiongraph:
    handler: backend.schedule.fg_handler.daily
    # S4: 委托权限留空，不配 APIG
    delegation_permissions: []

# ============ 采集 ============
collector:
  provider: html                    # 默认 html | 可替换: rss | fixture
  timeout_seconds: 15
  retries: 2
  user_agent: "Mozilla/5.0 (compatible; policy-daily/1.0)"
  max_items_per_source: 30
  text_only: true                   # H2：只取文字
  skip_images: true                 # H2：不下载图片
  sources_file: backend/sources.yaml
  html:
    encoding: auto
  rss:
    feed_limit: 50
  fixture:
    path: backend/tests/fixtures/

# ============ 处理 ============
processor:
  provider: deepseek                # 默认 deepseek | 可替换: rule
  categories: [民生, 经济金融, 科技产业, 安全国防, 政务法治, 其他]
  max_items: 20
  deepseek:
    model: deepseek-v4.1-flash      # DeepSeek V4.1 Flash
    base_url: https://api.deepseek.com/v1   # OpenAI 兼容
    api_key_env: DEEPSEEK_API_KEY   # S1：只读加密环境变量，不进代码/JSON/日志/Git
    temperature: 0.2
    max_tokens: 4096
    timeout_seconds: 60
    retries: 1
    prompts:
      classify: backend/prompts/classify.txt
      summarize: backend/prompts/summarize.txt
  rule:
    keyword_map:
      民生: [医保, 托育, 社保, 养老, 教育]
      经济金融: [融资, 利率, 货币政策, 税费]
      科技产业: [人工智能, 芯片, 制造业, 数据要素]
      安全国防: [国防, 安全, 应急]
      政务法治: [行政许可, 法规, 清单]
      其他: []

# ============ 归档 ============
archive:
  provider: local                   # 默认 local | 可替换: remote
  dir: reports/
  write_index: true
  atomic_write: true                # 先写 .tmp 再 rename
  keep_days: 0                      # 0 = 永久保留
  public_base_url: https://example.com/reports   # 推送里出现的 JSON 地址前缀
  remote:
    bucket: policy-reports
    endpoint: https://obs.example.com
    access_key_env: OBS_AK
    secret_key_env: OBS_SK

# ============ 推送 ============
publisher:
  provider: ntfy                    # 默认 ntfy | 可替换: noop
  ntfy:
    server: https://ntfy.sh
    topic_env: NTFY_TOPIC           # S2：长随机 topic，只存环境变量
    token_env: NTFY_TOKEN           # S2：Bearer Token，不进仓库/日志
    auth_scheme: Bearer
    title_template: "民生政策日报 {date}"
    body_char_min: 1000             # 正文 1000~1200 中文字
    body_char_max: 1200
    max_payload_bytes: 4096         # UTF-8 ≤ 4096
    priority: default
    tags: [memo]
    include_json_url: true          # 正文末尾附 JSON 地址

# ============ 客户端 ============
client:
  # S1/S3：不含 DeepSeek Key、不含 ntfy token、不含任何密钥
  base_url: https://example.com/reports   # 只读 JSON 源
  refresh_url_template: "https://ntfy.sh/{topic}"   # 订阅地址由用户手动填入，运行时不落盘 token
  android:
    permissions: [INTERNET]         # H3：仅此一项
    request_camera: false
    request_storage: false
    request_location: false
    request_contacts: false
    request_microphone: false
    request_sms: false
    request_phone: false
    request_bluetooth: false
    request_nfc: false
    db_path: private                # H3：SQLite 放私有目录
    background_service: false       # H4：不做后台常驻
  windows:
    window_title: 民生政策日报
    width: 1100
    height: 720
    pane_date_px: 200
    pane_category_px: 120
    pane_items: auto
    db_path: "%APPDATA%/PolicyDaily/reports.db"
  sync:
    fetch_timeout_seconds: 15
    fetch_retries: 2
    fill_missing_dates: true        # H5：补拉缺失日期
    empty_text: 今日暂无更新
```

---

## 5. UI 规范

通用：简约、无动画、无阴影、无圆角装饰、无渐变；单色块 + 细分隔线（1px `#E5E5E5`）。
空分类隐藏，不显示占位。

### 5.1 颜色（三级）

| 级别 | 用途 | 值 |
|---|---|---|
| 主色 | 标题栏/选中态/链接 | `#1A56DB` |
| 正文色 | 正文文本 | `#1F2933` |
| 辅助色 | 日期/来源/次要说明 | `#6B7280` |
| 背景 | 页面底色 | `#FFFFFF` |
| 分隔线 | 1px 分割 | `#E5E5E5` |
| 空状态文字 | 空中性灰 | `#9CA3AF` |

### 5.2 字号（三级）

| 级别 | 用途 | 字号 / 行高 | 字重 |
|---|---|---|---|
| 一级 | 页面标题、条目标题 | 16px / 24px | 600 |
| 二级 | 摘要正文、列表项 | 14px / 22px | 400 |
| 三级 | 日期、来源、分类标签 | 12px / 18px | 400 |

### 5.3 Windows 三栏布局

```
┌──────────────┬────────────┬─────────────────────────────────┐
│ 日期 200px   │ 分类 120px │ 条目 自适应（flex/拉伸）         │
│              │            │                                 │
│ 2026-11-11 ▸ │ 民生  2    │ 标题（一级 16/600）              │
│ 2026-11-10   │ 经济金融 1 │ 摘要（二级 14/400，最多3行省略） │
│ 2026-11-09   │ 政务法治 1 │ 来源 · 日期（三级 12/400）       │
│              │ (空分类隐藏)│                                 │
└──────────────┴────────────┴─────────────────────────────────┘
```

- 日期栏固定 `width: 200px`；分类栏固定 `width: 120px`；条目栏 `flex: 1` 自适应。
- 默认窗口 1100×720，最小 800×500。
- 选中日期/分类底色 `#1A56DB` 10% 透明度，无阴影无过渡。
- 条目 URL 用主色，点击交系统浏览器打开。

### 5.4 Android 两段布局

```
┌───────────────────────────────────────┐
│ [2026-11-11 ▾]                        │  ← 第一段：顶部日期（一级 16/600）
├───────────────────────────────────────┤
│ [民生2][经济金融1][政务法治1] ...      │  ← 第二段：横向分类（三级 12，可横滑，选中主色）
├───────────────────────────────────────┤
│ 标题（一级 16/600）                    │
│ 摘要（二级 14/400，最多3行省略）        │
│ 来源 · 日期（三级 12/400）             │
│ ─────────────────────────             │
│ ...条目列表（可滚动）                  │
└───────────────────────────────────────┘
```

- 第一段固定高度 56dp（日期选择器，点击弹出日期列表）。
- 第二段固定高度 44dp，横向滚动，空分类隐藏。
- 剩余区域为条目列表，纵向滚动。
- 无底部导航、无 FAB、无动画。

### 5.5 空状态文案

| 场景 | 文案 | 样式 |
|---|---|---|
| 当日无数据 / 无条目 | 今日暂无更新 | 三级 12/400，`#9CA3AF`，居中 |
| 分类下无条目 | （隐藏分类，不出现空文案） | — |
| 断网且无本地历史 | 今日暂无更新 | 同上 |
| 断网但有本地历史 | 顶部细条提示：网络不可用，显示本地缓存 | 三级 12/400，`#6B7280` |

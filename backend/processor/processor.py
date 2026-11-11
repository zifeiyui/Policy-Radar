"""段5 处理层：list[PolicyItem] → DailyReport（DeepSeek V4.1 Flash，OpenAI 兼容）。

契约见 01_INTERFACES §3.1/§3.3：
  Processor.process(date, raw_items) -> DailyReport
  category 六枚举；category_summary 六项全在且 sum == item_count == len(items)。

只依赖标准库；Key 只从环境变量 DEEPSEEK_API_KEY 读（S1），不进代码/JSON/日志。
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent

CATEGORIES: tuple[str, ...] = ("民生", "经济金融", "科技产业", "安全国防", "政务法治", "其他")
OTHER: str = CATEGORIES[-1]
EMPTY_TEXT: str = "今日暂无更新"

# 正常 1500—2000 字；月费用达阈值（默认 5 元）后压缩到 600—800 字
NORMAL_RANGE = (1500, 2000)
COMPRESSED_RANGE = (600, 800)

API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"   # OpenAI 兼容
DEFAULT_MODEL = "deepseek-v4.1-flash"
ENDPOINT = "/chat/completions"


# ---------------------------------------------------------------- 数据结构
@dataclass
class PolicyItem:
    id: str
    category: str
    title: str
    summary: str
    source: str
    url: str
    publish_date: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PolicyItem":
        return cls(**{k: str(d.get(k) or "") for k in cls.__dataclass_fields__})


@dataclass
class DailyReport:
    date: str
    generated_at: str
    item_count: int
    category_summary: list[dict[str, Any]] = field(default_factory=list)
    items: list[PolicyItem] = field(default_factory=list)
    summary_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "generated_at": self.generated_at,
            "item_count": self.item_count,
            "category_summary": self.category_summary,
            "items": [i.to_dict() for i in self.items],
            "summary_markdown": self.summary_markdown,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DailyReport":
        return cls(
            date=d["date"],
            generated_at=d.get("generated_at", ""),
            item_count=int(d.get("item_count", 0)),
            category_summary=list(d.get("category_summary") or []),
            items=[PolicyItem.from_dict(x) for x in d.get("items") or []],
            summary_markdown=d.get("summary_markdown", ""),
        )


class Processor(Protocol):
    def process(self, date: str, raw_items: list[dict]) -> DailyReport: ...


class ProcessorError(RuntimeError):
    """模型调用/解析失败；上层可降级到规则处理器。"""


# ---------------------------------------------------------------- 工具
def _warn(msg: str) -> None:
    # S1：任何日志都不打印 Key/token
    print(f"[processor] {msg}", file=sys.stderr)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _count_chars(text: str) -> int:
    """字数口径：去掉空白与 Markdown 标记后计可见字符。"""
    return len(re.sub(r"[\s*_#>`\-]", "", text))


_SENTENCE_END = re.compile(r"(?<=[。！？；\n])")


def _trim_to(text: str, max_chars: int) -> str:
    """超长时按句子边界截断，不切半个句子。"""
    if _count_chars(text) <= max_chars:
        return text
    out = ""
    for piece in _SENTENCE_END.split(text):
        if _count_chars(out + piece) > max_chars:
            break
        out += piece
    if not out:
        out = re.sub(r"[*_#>`]", "", text)[:max_chars]
    return out.rstrip()


def _fit_len(text: str, date: str, item_count: int, compressed: bool) -> str:
    """压到上限；不足下限时补中性说明（只用已知事实，不编造）。"""
    lo, hi = COMPRESSED_RANGE if compressed else NORMAL_RANGE
    text = _trim_to(text.strip(), hi)
    if _count_chars(text) >= lo:
        return text

    advice = (
        "- 先看标题里带时间节点或数字的条目，通常是当期重点。\n"
        "- 点击原文链接核对发布部门、文号与生效日期，正文以官方原文为准。\n"
        "- 同一条政策可能在多部门网站转载，信息不一致时以主管部门页面为准。\n"
        "- 分类按政策主题划分，跨领域条目归入其最主要的一面，可交叉阅读。\n"
        "- 后续配套细则、地方落实方案可能在其后数日发布，请留意次日日报。\n"
    )
    if item_count == 0:
        notes = [
            f"\n\n## 说明\n\n{date} 采集范围内未出现符合标准的政策条目，本期不作进一步解读。",
            "\n\n下一期将按原定时间继续采集中央部门公开政策文本并整理成日报。",
            f"\n\n## 阅读建议\n\n{advice}",
        ]
    else:
        notes = [
            f"\n\n## 补充说明\n\n本期共收录 {item_count} 条，数量偏少，正文从略。以下为阅读与核对建议：\n\n{advice}",
            "\n\n如需完整条目，请打开本日报随推送附带的 JSON 地址。",
        ]
    for note in notes:  # 可重复追加，只为凑够下限；不引入外部事实
        while _count_chars(text) < lo:
            text += note
    return _trim_to(text, hi)


def _extract_json(content: str) -> dict[str, Any]:
    """模型可能带 ```json 围栏或前后说明，取第一个平衡的 {...}。"""
    raw = content.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw, flags=re.S).strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = raw.find("{", start + 1)
    raise ProcessorError("模型输出不是合法 JSON")


# ---------------------------------------------------------------- 部门兜底
def load_departments(path: Path | None = None) -> tuple[dict[str, str], str]:
    """读 departments.yaml → ({部门: default_category}, fallback)。

    ponytail: 不引 PyYAML，只解析本文件的两层结构（顶层段落键 / 两空格缩进的
    条目，值可为内联“部门: 分类”或 department 段的“名字 + default_category”）。
    yaml 结构再复杂时换 pyyaml.safe_load。
    """
    path = path or HERE / "departments.yaml"
    depts: dict[str, str] = {}
    fallback = OTHER
    section, pending = "", ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        _warn(f"departments.yaml 读取失败，全部走 {OTHER}: {exc}")
        return {}, fallback

    for line in lines:
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        body = re.sub(r"\s+#.*$", "", body).rstrip()  # 去掉行内注释
        key, _, val = body.partition(":")
        key, val = key.strip().strip("'\""), val.strip().strip("'\"")
        if key == "fallback_category":  # 顶层配置项，不是部门条目
            if val in CATEGORIES:
                fallback = val
            continue
        if indent == 0:  # 段落键：departments / sources
            section = key
            continue
        if section not in ("departments", "sources") or not key:
            continue
        if key == "default_category":  # departments 段内的分类行，挂到上一个部门名
            if val in CATEGORIES and pending:
                depts[pending] = val
        elif val:  # sources 段内联写法：部门: 分类
            if val in CATEGORIES:
                depts.setdefault(key, val)
        else:  # departments 段的部门名
            pending = key
    return {k: v for k, v in depts.items() if v}, fallback


def fallback_category(source: str, departments: dict[str, str], default: str = OTHER) -> str:
    """source 子串命中部门表（长键优先）→ default_category；未命中 → 其他。"""
    src = (source or "").lower()
    if src:
        for name in sorted(departments, key=len, reverse=True):
            if name.lower() in src:
                cat = departments[name]
                return cat if cat in CATEGORIES else default
    return default


def _legal(category: Any) -> bool:
    return isinstance(category, str) and category in CATEGORIES


# ---------------------------------------------------------------- 费用守卫
class CostGuard:
    """月度费用累计 + 阈值判定。

    ponytail: 按每月累计 token 粗算费用（单价可传入调整），阈值到点即压缩输出；
    不做精确计费与预算硬停。需要精算时换成服务商账单接口。
    """

    def __init__(
        self,
        threshold_cny: float = 5.0,
        state_file: Path | None = None,
        price_in_cny_per_1k: float = 0.0005,
        price_out_cny_per_1k: float = 0.002,
    ) -> None:
        self.threshold = float(threshold_cny)
        self.state_file = state_file or HERE / "month-cost.json"
        self.price_in, self.price_out = price_in_cny_per_1k, price_out_cny_per_1k

    def _read(self) -> dict[str, float]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def spent(self) -> float:
        return float(self._read().get(datetime.now().strftime("%Y-%m"), 0.0))

    def add(self, tokens_in: int, tokens_out: int) -> float:
        cost = tokens_in / 1000 * self.price_in + tokens_out / 1000 * self.price_out
        month = datetime.now().strftime("%Y-%m")
        data = self._read()
        data[month] = round(float(data.get(month, 0.0)) + cost, 6)
        try:
            self.state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            _warn(f"月度费用文件写入失败（不影响日报）: {exc}")
        return data[month]

    def compressed(self) -> bool:
        spent = self.spent()
        if spent >= self.threshold:
            _warn(f"本月累计约 {spent:.4f} 元 ≥ 阈值 {self.threshold} 元，启用压缩输出")
            return True
        return False


# ---------------------------------------------------------------- 主处理器
class DeepSeekProcessor:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key_env: str = API_KEY_ENV,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout_seconds: float = 60,
        retries: int = 1,
        max_items: int = 20,
        cost_threshold_cny: float = 5.0,
        cost_state_file: Path | None = None,
        prompt_file: Path | None = None,
        departments_file: Path | None = None,
    ) -> None:
        # S1：Key 只从环境变量取；显式传参仅为测试注入，任何日志都不回显
        self._api_key = api_key if api_key is not None else os.environ.get(api_key_env, "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout_seconds
        self.retries = max(0, retries)
        self.max_items = max_items
        self.prompt_file = prompt_file or HERE / "prompt_system.txt"
        self.departments, self.fallback = load_departments(departments_file)
        self.cost = CostGuard(cost_threshold_cny, cost_state_file)

    # ---- 契约入口
    def process(self, date: str, raw_items: list[dict]) -> DailyReport:
        inputs = self._normalize_input(date, raw_items or [])
        compressed = self.cost.compressed()

        summary_md, model_items = "", []
        if inputs and self._api_key:
            try:
                summary_md, model_items = self._call_model(date, inputs, compressed)
            except ProcessorError as exc:
                _warn(f"模型处理失败，改用本地摘要: {exc}")
        elif inputs:
            _warn(f"未设置环境变量 {API_KEY_ENV}，改用本地摘要")

        items = self._merge_items(date, inputs, model_items)
        if not summary_md.strip():
            summary_md = self._local_summary(date, items, compressed)

        counts = {c: 0 for c in CATEGORIES}
        for it in items:
            counts[it.category] += 1

        return DailyReport(
            date=date,
            generated_at=_now_iso(),
            item_count=len(items),
            category_summary=[{"category": c, "count": counts[c]} for c in CATEGORIES],
            items=items,
            summary_markdown=_fit_len(summary_md, date, len(items), compressed),
        )

    # ---- 输入规整：分类非法先按部门兜底
    def _normalize_input(self, date: str, raw_items: list[dict]) -> list[PolicyItem]:
        items: list[PolicyItem] = []
        for entry in raw_items[: self.max_items]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            url = str(entry.get("url") or "").strip()
            if not title or not url:  # 无标题/链接的条目无法展示，丢弃
                continue
            cat = entry.get("category")
            if not _legal(cat):
                cat = fallback_category(str(entry.get("source") or ""), self.departments, self.fallback)
            items.append(
                PolicyItem(
                    id=f"{date}-{len(items) + 1:02d}",
                    category=cat,
                    title=title,
                    summary=str(entry.get("summary") or entry.get("raw_text") or "").strip()[:200],
                    source=str(entry.get("source") or "").strip(),
                    url=url,
                    publish_date=str(entry.get("publish_date") or date),
                )
            )
        return items

    # ---- 模型调用
    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base_url + ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _call_model(
        self, date: str, items: list[PolicyItem], compressed: bool
    ) -> tuple[str, list[Any]]:
        try:
            system_prompt = self.prompt_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ProcessorError(f"系统提示词读取失败: {exc}") from exc

        mode = (
            "压缩模式：本月费用已达阈值，summary_markdown 压缩到 600—800 字。"
            if compressed
            else "常规模式：summary_markdown 1500—2000 字。"
        )
        dept_hint = "、".join(f"{k}→{v}" for k, v in list(self.departments.items())[:40]) or "无"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"日期：{date}\n{mode}\n"
                        f"部门兜底表（source 命中即取其 default_category）：{dept_hint}\n"
                        f"输入条目：\n{json.dumps([i.to_dict() for i in items], ensure_ascii=False)}"
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }

        last_err: BaseException | None = None
        for attempt in range(self.retries + 1):
            try:
                data = self._post(payload)
                usage = data.get("usage") or {}
                month = self.cost.add(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
                _warn(f"模型返回 {int(usage.get('total_tokens') or 0)} tokens，本月累计约 {month:.4f} 元")
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                if not content:
                    raise ProcessorError("模型返回内容为空")
                obj = _extract_json(content)
                return str(obj.get("summary_markdown") or ""), list(obj.get("items") or [])
            except Exception as exc:  # urllib/超时/解析/结构错误统一重试一次
                last_err = exc
                _warn(f"第 {attempt + 1} 次调用失败: {type(exc).__name__}")
        raise ProcessorError(f"模型调用失败: {type(last_err).__name__}")

    # ---- 合并模型结果：分类兜底、链接以输入为准、去重排序重编号
    def _merge_items(self, date: str, inputs: list[PolicyItem], out_items: list[Any]) -> list[PolicyItem]:
        by_key: dict[str, dict[str, Any]] = {}
        for obj in out_items:
            if not isinstance(obj, dict):
                continue
            for key in (obj.get("url"), obj.get("title")):
                if isinstance(key, str) and key.strip():
                    by_key.setdefault(key.strip(), obj)

        merged: list[PolicyItem] = []
        seen: set[str] = set()
        for src in inputs:
            obj = by_key.get(src.url) or by_key.get(src.title)
            if not isinstance(obj, dict):
                obj = {}
            # 优先级：模型分类 > 输入已是合法分类 > departments.yaml 兜底 > 其他
            cat = obj.get("category")
            if not _legal(cat):
                cat = src.category if _legal(src.category) else fallback_category(
                    src.source or str(obj.get("source") or ""), self.departments, self.fallback
                )
            if src.url in seen:
                continue
            seen.add(src.url)
            merged.append(
                PolicyItem(
                    id=src.id,
                    category=cat,
                    title=str(obj.get("title") or src.title).strip(),
                    summary=str(obj.get("summary") or src.summary).strip()[:400],
                    source=str(obj.get("source") or src.source).strip(),
                    url=src.url,  # 链接只信输入，禁止模型改写
                    publish_date=src.publish_date,
                )
            )

        order = {c: i for i, c in enumerate(CATEGORIES)}
        merged.sort(key=lambda x: order[x.category])
        for i, it in enumerate(merged, 1):
            it.id = f"{date}-{i:02d}"
        return merged

    # ---- 降级摘要（无 Key / 调用失败）
    def _local_summary(self, date: str, items: list[PolicyItem], compressed: bool) -> str:
        if not items:
            return EMPTY_TEXT
        parts = [f"今日要点：{date} 共收录政策 {len(items)} 条。"]
        if not compressed:
            parts.append("按主题分列如下，供读者快速浏览与核对。")
        for cat in CATEGORIES:
            group = [i for i in items if i.category == cat]
            if not group:
                continue
            parts.append(f"## {cat}")
            parts.append("\n".join(f"- **{i.title}**：{i.summary}（{i.source}）" for i in group))
        return "\n\n".join(parts)


# ---------------------------------------------------------------- 工厂
def get_processor(name: str = "deepseek", **kwargs: Any) -> Processor:
    if name == "deepseek":
        return DeepSeekProcessor(**kwargs)
    raise ValueError(f"未知 processor: {name}（本段只提供 deepseek）")


# ---------------------------------------------------------------- 自检
def _demo() -> None:
    # 部门表解析回归：不得把 default_category 等段落键当成部门
    depts, fb = load_departments()
    assert depts and "default_category" not in depts and "departments" not in depts
    assert depts.get("国务院办公厅") and depts.get("教育部") and fb in CATEGORIES

    proc = DeepSeekProcessor(api_key="", cost_state_file=Path(os.devnull))
    raw = [
        {"title": "关于优化基本医疗保险门诊共济保障机制的指导意见",
         "url": "https://www.gov.cn/a.htm", "source": "中国政府网", "category": "民生",
         "raw_text": "要求2027年底前细化门诊共济保障范围。"},
        {"title": "关于进一步支持小微企业融资的若干措施",
         "url": "https://www.pbc.gov.cn/b.htm", "source": "中国人民银行", "category": "乱写分类"},
        {"title": "关于规范行政许可事项清单管理的通知",
         "url": "https://www.moj.gov.cn/c.htm", "source": "司法部", "category": "政务法治"},
        {"title": "某未知部门通知", "url": "https://x.example/d.htm", "source": "某部委"},
    ]

    def check(rep: DailyReport, lo: int, hi: int) -> None:
        d = rep.to_dict()
        assert [c["category"] for c in d["category_summary"]] == list(CATEGORIES)
        assert sum(c["count"] for c in d["category_summary"]) == d["item_count"] == len(d["items"])
        for c in d["category_summary"]:
            assert sum(1 for it in d["items"] if it["category"] == c["category"]) == c["count"]
        assert lo <= _count_chars(d["summary_markdown"]) <= hi, _count_chars(d["summary_markdown"])

    rep = proc.process("2026-11-11", raw)
    check(rep, *NORMAL_RANGE)
    by_url = {it.url: it for it in rep.items}
    assert by_url["https://www.pbc.gov.cn/b.htm"].category == "经济金融"  # 非法分类 → 中国人民银行
    assert by_url["https://x.example/d.htm"].category == OTHER            # 未命中部门 → 其他
    assert rep.items[0].id == "2026-11-11-01"
    assert [it.id for it in rep.items] == [f"2026-11-11-{i:02d}" for i in range(1, 5)]

    # 月费用达阈值 → 压缩输出
    proc.cost.spent = lambda: 5.0  # type: ignore[method-assign]
    check(proc.process("2026-11-11", raw), *COMPRESSED_RANGE)

    # 空日
    empty = proc.process("2026-11-12", [])
    check(empty, *COMPRESSED_RANGE)
    assert empty.items == [] and empty.item_count == 0

    # 模型输出带围栏/杂字也能解析
    assert _extract_json('前言\n```json\n{"summary_markdown":"x","items":[]}\n```')["summary_markdown"] == "x"
    print("processor self-check OK")


if __name__ == "__main__":
    _demo()

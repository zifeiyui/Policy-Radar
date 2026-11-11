"""段4 直采部门注册表：按配置启用/禁用。

DEPARTMENT_COLLECTORS: 部门全称 → 收集器类。
enabled_collectors(cfg) 读取 config.yaml 的 collector.departments：
  缺省（不存在该键）→ 全部启用；显式列出 → 只启用清单内部门；
  某部门 enabled: false → 禁用。
"""

from __future__ import annotations

import logging

try:  # 包内导入 / 脚本直跑
    from .mohrss_collector import MohrssCollector
except ImportError:  # pragma: no cover
    from mohrss_collector import MohrssCollector

log = logging.getLogger(__name__)

DEPARTMENT_COLLECTORS = {
    MohrssCollector.source_name: MohrssCollector,
}


def _entries(cfg: dict) -> dict | list | None:
    """兼容 config.yaml 的 collector.departments。"""
    if not isinstance(cfg, dict):
        return None
    col = cfg.get("collector")
    if not isinstance(col, dict):
        return None
    return col.get("departments")


def enabled_collectors(cfg: dict | None = None) -> list[type]:
    """返回本次运行应启用的收集器类，顺序同 DEPARTMENT_COLLECTORS。"""
    deps = _entries(cfg or {})
    if deps is None:
        return list(DEPARTMENT_COLLECTORS.values())

    if isinstance(deps, list):  # 白名单写法
        allow = {str(d).strip() for d in deps}
        return [cls for name, cls in DEPARTMENT_COLLECTORS.items() if name in allow]

    if isinstance(deps, dict):  # {部门: {enabled: bool}} 写法
        out = []
        for name, cls in DEPARTMENT_COLLECTORS.items():
            spec = deps.get(name)
            if spec is None:
                continue
            if isinstance(spec, dict) and spec.get("enabled", True) is False:
                continue
            if spec is False:
                continue
            out.append(cls)
        return out

    log.warning("collector.departments 格式无法识别，按全部启用处理")
    return list(DEPARTMENT_COLLECTORS.values())


def collect_all(date: str, cfg: dict | None = None, fulltext: bool = True) -> list[dict]:
    """依次跑启用部门；单部门失败已由基类吞掉，这里再加一层保险。"""
    items: list[dict] = []
    for cls in enabled_collectors(cfg):
        c = cls()
        try:
            items.extend(c.collect(date, fulltext=fulltext))
        except Exception:  # noqa: BLE001
            log.exception("%s 采集整体失败", cls.__name__)
        finally:
            c.close()
    return items


if __name__ == "__main__":  # 自检
    assert enabled_collectors({}) == [MohrssCollector]
    assert enabled_collectors({"collector": {"departments": ["人力资源和社会保障部"]}}) == [MohrssCollector]
    assert enabled_collectors({"collector": {"departments": ["人社部"]}}) == []
    assert enabled_collectors(
        {"collector": {"departments": {"人力资源和社会保障部": {"enabled": False}}}}) == []
    assert enabled_collectors(
        {"collector": {"departments": {"人力资源和社会保障部": {"enabled": True}}}}) == [MohrssCollector]


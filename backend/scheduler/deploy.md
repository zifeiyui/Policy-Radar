# 段2 调度层部署（FunctionGraph 定时器）

1. 新建函数：运行时 Python 3.10+，上传 `handler.py`，入口 `handler.handler`；依赖用官方 requests 层或把 `requirements.txt` 依赖打包成 ZIP 上传。
2. 配环境变量：`PIPELINE_URL`（后端流水线入口，必填）、`PIPELINE_TIMEOUT=15`、`PIPELINE_RETRIES=3`。
3. 加定时触发器 Cron：六段式 `0 1 18 * * ?`（每天 18:01，时区 Asia/Shanghai）。
4. **委托权限留空，不配 APIG 触发器**（S4）。
5. 保存并测试：日志应出现 `run_id=... status=ok upstream_status=200`。

"""段2 调度层：FunctionGraph 定时器入口（18:01）→ POST PIPELINE_URL。

S4：委托权限留空，不配 APIG 触发器。
"""
import logging
import os
import time
import uuid
from datetime import datetime

import requests

PIPELINE_URL = os.environ.get("PIPELINE_URL", "https://example.com/run")
TIMEOUT = int(os.environ.get("PIPELINE_TIMEOUT", "15"))
RETRIES = int(os.environ.get("PIPELINE_RETRIES", "3"))
# ponytail: 固定间隔重试，够用；要退避再加 RETRY_BACKOFF 环境变量
RETRY_BACKOFF = int(os.environ.get("PIPELINE_RETRY_BACKOFF", "2"))

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    rid = (context and getattr(context, "getRequestID", lambda: None)()) or uuid.uuid4().hex
    started = datetime.now().isoformat(timespec="seconds")
    log.info("run_id=%s start=%s url=%s", rid, started, PIPELINE_URL)

    status, upstream = "failed", None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(PIPELINE_URL, json={"run_id": rid, "trigger": "timer"},
                              timeout=TIMEOUT)
            upstream = r.status_code
            if 200 <= upstream < 300:
                status = "ok"
                break
            log.warning("run_id=%s attempt=%d upstream_status=%s", rid, attempt, upstream)
        except Exception as e:  # noqa: BLE001 - 调度层不向上抛，避免平台重试风暴
            log.warning("run_id=%s attempt=%d error=%s", rid, attempt, e)
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF)

    log.info("run_id=%s end=%s status=%s upstream_status=%s",
             rid, datetime.now().isoformat(timespec="seconds"), status, upstream)
    return {"status": status, "upstream_status": upstream, "run_id": rid, "started_at": started}


if __name__ == "__main__":  # 自检：ok / 500 重试 / 连接异常 三条路径
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    hits = []
    code = [200]

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(1)
            self.send_response(code[0]); self.end_headers()
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    PIPELINE_URL = f"http://127.0.0.1:{srv.server_port}/run"

    retries, backoff = RETRIES, RETRY_BACKOFF
    RETRIES, RETRY_BACKOFF = 1, 0
    assert handler({}, None)["status"] == "ok" and handler({}, None)["upstream_status"] == 200
    code[0] = 500
    assert handler({}, None) == {**handler({}, None), "status": "failed", "upstream_status": 500}
    RETRIES, RETRY_BACKOFF = 3, 0
    hits.clear()
    assert handler({}, None)["status"] == "failed" and len(hits) == 3
    srv.shutdown()
    PIPELINE_URL = "http://127.0.0.1:1/run"
    res = handler({}, None)
    assert res["status"] == "failed" and res["upstream_status"] is None
    RETRIES, RETRY_BACKOFF = retries, backoff
    print("self-check passed")

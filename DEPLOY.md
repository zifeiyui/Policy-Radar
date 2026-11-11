# DEPLOY

## 一、打包（5 步）

1. 推 tag：`git tag v1.0.0 && git push origin v1.0.0`（或 Actions 页手动 `workflow_dispatch`）。
2. 等 `android` 任务跑完；`aapt 权限检查` 通过 = APK 只有 `INTERNET`。
3. 等 `windows` 任务跑完，产出 `PolicyDaily.exe`。
4. 在 Actions 运行页下载 `android-apk` 和 `windows-exe` 两个 artifact。
5. 安装 APK / 双击 exe，把 `base_url` 填成第 3 步的后端地址。

## 二、后端部署（3 步）

1. 上传仓库：`git push` 到 Git 托管，后端机器 `git clone` 到 `/opt/policy-daily`。
2. 装依赖并配密钥（只放环境变量，S1/S2）：
   ```
   pip install -r backend/requirements.txt
   export DEEPSEEK_API_KEY=... NTFY_TOPIC=... NTFY_TOKEN=...
   ```
3. 起 cron 并对外暴露 reports/（只读静态）：
   ```
   (crontab -l; echo '1 18 * * * cd /opt/policy-daily && python -m backend.app') | crontab -
   cd reports && python -m http.server 8080
   ```
   `config.yaml` 里 `archive.public_base_url` 改成 `http://<你的域名或IP>:8080`。

## 三、CORS（5 行，二选一）

1. Windows exe 走 Python requests，不受 CORS 限制；Android 走原生 HTTP，也不受 CORS 限制。
2. 所以默认**不开** CORS。
3. 仅当客户端换成浏览器网页版，才需要给 JSON 响应加：
   ```
   Access-Control-Allow-Origin: *
   Access-Control-Allow-Methods: GET
   ```
4. 加的方式：在两个 JSON 地址前加反向代理（nginx `add_header`），或 `python -m http.server` 换成带这两个头的静态服务。
5. 不加的后果：网页版能下载 JSON 但 AJAX 读不到；APK/exe 完全正常。

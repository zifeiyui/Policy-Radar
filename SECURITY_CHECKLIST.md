# SECURITY_CHECKLIST — 段9 安全加固

> 逐项勾选。任一 ❌ 不得上线。对应约束：S1 / S2 / S3 / S4 / S5、H3。

---

## 1. DeepSeek Key 设置（S1）

- [ ] Key 只放后端加密环境变量 `DEEPSEEK_API_KEY`，代码只读 `os.environ`。
- [ ] 不写入 `config.yaml`、不写 `.env` 明文、不进 Git（`.gitignore` 含 `.env`）。
- [ ] 日志脱敏：任何打印/异常栈不出现 Key，错误信息只写状态码。
- [ ] 平台泄漏响应里不回显请求头；Key 轮换后旧 Key 立即在控制台吊销。
- [ ] 磁盘加密/密钥托管（如 KMS、FunctionGraph 加密环境变量）开启。

## 2. ntfy Token 设置（S2）

- [ ] Topic 长随机（≥32 字符不可猜），只存环境变量 `NTFY_TOPIC`。
- [ ] 发送 Token 只存 `NTFY_TOKEN`，用 `Authorization: Bearer`，不入仓库/日志。
- [ ] 若自建 ntfy 或使用 ntfy.sh 付费 ACL：topic 设为写权限受限，读订阅单独凭据。
- [ ] 客户端订阅地址由用户手动填入，运行时不落盘 token（S3）。
- [ ] 推送正文检查：只含标题/摘要/JSON URL，无 Key、无 token、无内部地址。

## 3. FunctionGraph 权限（S4）

- [ ] 函数委托授权 `delegation_permissions: []`，留空，不绑定 APIG/API 网关。
- [ ] 函数执行角色只授最小权限：读采集出口 + 写 `reports/` + 发 ntfy，不含 OBS 管理权限。
- [ ] 函数环境变量用「加密变量」存放 Key/token，禁止在代码常量里硬编码。
- [ ] 出站访问白名单/固定出口，仅允许：采集源域名、`api.deepseek.com`、ntfy 服务器。
- [ ] 定时触发器 18:01（Asia/Shanghai），不开放公网 HTTP 入口（无 APIG、无函数 URL 暴露）。

## 4. 客户端无密钥（S3 / H3）

- [ ] Android / Windows 源码全局搜索 `DEEPSEEK`、`sk-`、`Bearer`、`NTFY_TOKEN`：结果为空。
- [ ] 客户端配置只有 `base_url` + `refresh_url`，无任何密钥字段。
- [ ] Android `AndroidManifest.xml` 只声明 `android.permission.INTERNET`，`aapt dump permissions` 复核。
- [ ] SQLite 只落应用私有目录；APK/exe 不含 token 明文（打包后再扫一次 strings）。
- [ ] 客户端只读：无上传接口、无模型调用、无后台上报（H4）。

## 5. 静态托管关闭目录列表（S5）

- [ ] 对象存储/静态站点关闭 ListBucket / 目录列表（OBS bucket policy 禁止 `s3:ListBucket` 匿名）。
- [ ] 只放行 `GET {base}/{YYYY-MM-DD}.json` 与 `GET {base}/index.json`，其余路径 403。
- [ ] 禁止 `reports/` 下的 `.tmp`、`.git`、配置文件、日志可被匿名访问。
- [ ] CORS 仅允许客户端需要的源，或干脆不开放（GET 直连无需 CORS）。
- [ ] JSON 内容自检：仅 8 字段，无图片/base64/Key/token/内部 URL（S5、H2）。

## 6. 落地验收（上线前一次性）

- [ ] 全仓 `git grep -iE "sk-|Bearer [A-Za-z0-9]{10,}|NTFY_TOKEN\s*="` 无命中。
- [ ] 浏览器匿名访问 `{base}/`、`{base}/reports/` 都应 403，不能列出目录。
- [ ] 抓一次真实推送载荷，逐字节确认不含 Key/token，UTF-8 ≤ 4096 字节。
- [ ] 关闭调试日志、`DEBUG=false`，异常栈不外露到客户端。

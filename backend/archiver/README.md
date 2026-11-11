# 段6 归档层（archiver）

`archive(report) -> json_url`：写 `reports/YYYY-MM-DD.json` + `index.json`（倒序去重，保留 365 天），先 `.tmp` 再 rename，返回 HTTPS 地址。

环境变量：`REPORTS_DIR`（默认 `<APP>/reports`）、`PUBLIC_BASE_URL`（推送里出现的地址前缀，必须 `https://`）。
自检：`python backend/archiver/archiver.py`。

## CORS 注意事项

客户端（Android/webview、Windows）直接 `GET {base}/YYYY-MM-DD.json` 和 `GET {base}/index.json`，属于跨域请求。

### 1. 优先：GitHub Pages / Cloudflare Pages

这两者默认返回 `Access-Control-Allow-Origin: *`，**无需任何配置**，把 `reports/` 目录当静态站发布即可。
注意：GitHub **Raw** 地址（`raw.githubusercontent.com`）也带 `*`，但限速且不是站点语义，能用 Pages 就用 Pages。
私有仓库 / 需要缓存刷新时，Pages 每次部署生效即可；同一天重发会覆盖同名文件，客户端下次拉取拿到新内容。

### 2. 对象存储（OBS / S3 / COS / OSS）

必须显式配置桶 CORS 规则，否则浏览器/WebView 拉取被拦：

| 项 | 值 |
|---|---|
| AllowedOrigin | `*`（或客户端确切的域名列表） |
| AllowedMethod | `GET`（只需 GET；不要开 HEAD/PUT，除非客户端确实用） |
| AllowedHeader | `*` 或留空（客户端不发任何鉴权头，S3/S5） |
| ExposeHeader | `ETag`、`Content-Length`（可选，便于条件请求） |
| MaxAgeSeconds | `600` |

例（华为 OBS）：控制台“桶 → 权限管理 → CORS 规则”按上表填；S3 对应 `PUT /?cors` 的 `CORSRule`。
对象 `Content-Type` 必须是 `application/json; charset=utf-8`，否则部分 WebView 不按 UTF-8 解码中文。

### 3. 常见坑

- `Origin=*` 不能与 `Access-Control-Allow-Credentials: true` 同用；本工程不带 Cookie/凭据，别开 credentials。
- 客户端不要发 `Authorization`/自定义头，否则会触发预检 `OPTIONS`，静态站点常不响应 OPTIONS 而导致失败。
- 更新 `index.json` 后若走 CDN，需刷新缓存（或给 `index.json` 设 `Cache-Control: no-cache`），否则客户端看不到新日期。
- 域名必须 HTTPS：Android 9+ 默认禁明文 HTTP（仅 INTERNET 权限 + HTTPS 是 H3 前提）。

目标：每天18:01采集中央民生政策文字，DeepSeek Flash精炼成分类日报，18:05经ntfy推送“正文+JSON地址”到Android APK和Windows exe；客户端仅INTERNET权限，拉JSON存SQLite，可离线翻阅。
H1 18:01触发/18:05发送，属DeepSeek空闲时段。
H2 只处理文字，不下载图片、不做OCR，JSON不含图片/base64。
H3 Android仅INTERNET，不申请相机/相册/麦克风/定位/通讯录/短信/电话/蓝牙/NFC/存储；SQLite放私有目录。
H4 客户端只做：收推送或启动同步、拉JSON、存SQLite展示；不爬取、不调模型、不做后台常驻。
H5 每天写reports/YYYY-MM-DD.json + index.json；客户端补拉缺失日期，断网读已存历史。
H6 模块可拆卸，只通过接口通信，换实现只改配置。
S1 DeepSeek Key只存后端加密环境变量，不进客户端/JSON/日志/Git。
S2 ntfy用长随机topic + Bearer Token，token不进仓库/日志。
S3 客户端不硬编码任何Key/token。
S4 FunctionGraph委托权限留空，不配APIG。
S5 JSON只含摘要和原文链接，不含敏感信息。
分类枚举：民生、经济金融、科技产业、安全国防、政务法治、其他。
模型：DeepSeek V4.1 Flash，OpenAI兼容。
推送：ntfy，UTF-8≤4096字节，正文截断1000—1200中文字+JSON URL。
UI：简约无动画无阴影；Windows三栏（日期200px/分类120px/条目自适应），Android顶部日期+横向分类+条目列表；空分类隐藏；无数据显示“今日暂无更新”。

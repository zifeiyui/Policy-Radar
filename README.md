# 📡 Policy Radar (政策雷达)

基于 AI 的每日民生政策监控与摘要系统。自动采集最新公开文件，通过 DeepSeek 生成分类日报，并推送到跨平台客户端（Android/Windows）。

## 📥 下载客户端

[![Download APK](https://img.shields.io/badge/Download-Android_APK-green.svg?style=for-the-badge&logo=android)](https://github.com/zifeiyui/Policy-Radar/releases/latest)

> 💡 点击上方按钮前往 Releases 页面下载最新版测试包。安装时如果提示“未知来源”或“不安全应用”，请选择允许安装。

## ✨ 核心功能

- **全自动采集**：每天 18:01 自动抓取国务院及 26 个部门的政策文件。
- **AI 精炼汇总**：调用 DeepSeek 模型提取核心要点，生成分类日报。
- **极简双端体验**：基于 Flet 构建，支持 Android APK 与 Windows 桌面端。
- **离线历史归档**：客户端本地 SQLite 存储，断网也能翻阅往期记录。

## 🛠️ 技术架构

- **客户端**：Flet (Python) + SQLite
- **云端处理**：GitHub Actions + DeepSeek V4.1 Flash
- **推送通道**：ntfy

---
*当前为 Beta 测试版本，功能正在持续完善中。*

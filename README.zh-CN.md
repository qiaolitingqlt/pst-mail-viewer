# PST Mail Viewer — Windows 本地邮件查看器

不用安装 Outlook，也不需要 Microsoft 365 订阅，就可以在 Windows 上本地浏览 `.pst` 邮箱备份。支持邮件文件夹、搜索、HTML 正文预览和附件下载。

> **当前状态：测试版。** 已在一份实际的数 GB PST 文件上使用，但尚未完成广泛兼容性、安全审计和自动化测试。使用前请备份原始 PST 文件。

## 首次安装

1. 从 [Python 官网](https://www.python.org/downloads/release/python-31210/) 安装 **Python 3.12**（建议安装时勾选加入 PATH）。
2. 下载本项目 ZIP 并解压到本地文件夹。
3. 双击 **`install.bat`**，安装 `libpff-python`。需要联网安装一次。
4. 将你的 `.pst` 文件**拖到 `start.bat` 上**；也可以双击 `start.bat`，粘贴 PST 文件的完整路径。
5. 浏览器访问 **http://127.0.0.1:8765**。使用时不要关闭命令行窗口。

也可以在项目文件夹打开 CMD，执行：

```bat
py -3.12 app.py "D:\Backups\outlook-backup.pst"
```

## 功能与限制

- 按文件夹浏览邮件；按主题、发件人、收件人和**纯文本正文**搜索。
- 同一封邮件可以切换纯文本或 HTML 查看；HTML 被隔离，不加载外部图片或脚本。
- 按需下载附件，原始 PST 保持只读。
- 首次启动需建立 SQLite 索引；只要 PST 路径、大小和修改时间不变，以后复用已有索引。
- **仅 HTML 的正文目前不参与关键词搜索**；CID 内嵌图片可能无法显示。
- 当前仅提供个人使用级别的测试，不保证能读取所有 PST 变体或损坏的文件。

## 隐私警告（发布 GitHub 前必看）

**不要上传 `.pst`、`mail_index.sqlite3`、导出附件、私人邮件或带私人信息的截图。** `.gitignore` 已排除常见文件，但不会自动删除已经加入 Git 追踪的隐私文件。上传前务必检查仓库文件列表与 `git status`。

邮件索引可能含明文邮件，不要把索引文件发给别人。程序只在电脑本机监听 `127.0.0.1`，不需要将端口公开。附件本身也可能不安全，请谨慎打开。

## 常见错误

- `the following arguments are required: pst`：忘了提供 PST 文件路径。
- `ERR_CONNECTION_REFUSED`：命令窗口可能已经退出；请先看命令行报错。
- `No module named pypff`：用 Python 3.12 重新运行 `install.bat`。
- HTML 邮件不显示：切换到“HTML 原始排版”，仍不支持的邮件请提交去敏后的复现说明。

## 许可证

本仓库中的原创程序代码采用 **MIT License**。PST 解析依赖 `libpff-python` 为独立的 **LGPL-3.0-or-later** 开源项目，见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。此压缩包不包含该第三方库。

[English README](README.md)

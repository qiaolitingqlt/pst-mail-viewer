# PST Mail Viewer (Windows)

A simple **local, read-only Outlook PST archive viewer**. Browse folders, search mail, preview plain-text/HTML messages and download attachments without Outlook or a Microsoft 365 subscription. The server binds only to `127.0.0.1`, and does not upload mail.

> **Project status:** Small personal utility / beta. Successfully used with one real-world multi-GB PST archive; not yet tested with a broad range of PST files. Keep a backup of your original PST.

## Requirements

- Windows 10/11 (64-bit recommended)
- Python **3.12** (Windows installation with `py` launcher)
- Internet access *once* to install Python dependency
- `libpff-python` (version pinned in [`requirements.txt`](requirements.txt))

## Install and run

1. Download or clone this repository; extract it to a normal folder.
2. Install Python 3.12 from [python.org](https://www.python.org/downloads/release/python-31210/) if needed.
3. Double-click **`install.bat`** (or run `py -3.12 -m pip install -r requirements.txt`).
4. Drag your `.pst` file onto **`start.bat`**, or double-click `start.bat` and paste its full path.
5. Open **http://127.0.0.1:8765** (the script generally opens the browser automatically).
6. Keep the terminal open while using the viewer. Press `Ctrl+C` to stop.

Or run directly:

```bat
py -3.12 app.py "D:\Backups\outlook-backup.pst"
```

The first launch indexes the archive and may take time, especially for multi-GB PSTs. Later launches reuse `mail_index.sqlite3` when the PST path, size and modification time are unchanged. Index search covers **plain-text** message content and metadata; HTML-only body text is **not yet indexed for search**.

## Features

- Folder navigation and paged message list
- Subject/sender/recipient/plain-text substring search
- Plain-text preview, isolated HTML preview (sandboxed iframe)
- Download individual attachments on demand
- SQLite index stored locally; original PST opened read-only

## Privacy and safety

**NEVER commit PST files, the SQLite index, or extracted attachments.** These can contain names, addresses, passwords, private messages, and other sensitive information. `.gitignore` excludes common patterns, but always inspect `git status` before publishing. Already-tracked files are *not* removed by `.gitignore`.

HTML messages are shown in a sandboxed iframe with a restrictive Content Security Policy to block scripts and network resources. Some remote/embedded images and rich layouts will not render. Do not expose the local server to the internet. Attachments should be treated as untrusted files.

The app does not delete or change the original PST. **Index creation and storage use local disk space and may contain unencrypted email content**. Delete `mail_index.sqlite3` only if you intend to rebuild the index next time.

## Troubleshooting

- `the following arguments are required: pst`: supply a quoted full PST path or drag the PST onto `start.bat`.
- `ERR_CONNECTION_REFUSED`: check whether the command window shows an error; the server must stay running.
- `No module named pypff`: run `install.bat` with Python 3.12.
- HTML-only mail content: click **HTML original layout**. CID inline images may still be absent.
- Want a fresh index? Stop the server, back up then delete `mail_index.sqlite3`, and restart.

## License and third-party notices

The original application code in this repository is released under the **MIT License** (see [`LICENSE`](LICENSE)). It depends on [`libpff-python`](https://pypi.org/project/libpff-python/), an **independent LGPL-3.0-or-later** library; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). The dependency is installed from PyPI and is **not bundled in this ZIP**.

See [中文说明](README.zh-CN.md).

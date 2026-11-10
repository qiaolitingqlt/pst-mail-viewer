# -*- coding: utf-8 -*-
"""Local, read-only PST browser. Python 3.12+, libpff-python. No cloud access."""
import argparse
import datetime as dt
import html
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

try:
    import pypff
except ImportError:
    print('缺少 pypff，请先运行 安装依赖.bat 或 py -3.12 -m pip install libpff-python')
    sys.exit(1)

BASE = Path(__file__).resolve().parent
DB = BASE / 'mail_index.sqlite3'
PST = None
PST_LOCK = threading.RLock()
STATE = {'status': 'idle', 'processed': 0, 'errors': 0, 'detail': '', 'ready': False}


def safe_text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        for enc in ('utf-8', 'utf-16-le', 'gb18030', 'cp1252'):
            try:
                return value.decode(enc).rstrip('\x00')
            except UnicodeError:
                continue
        return value.decode('utf-8', errors='replace')
    return str(value)


def prop(obj, key, default=None):
    try:
        v = getattr(obj, key)
        return v() if callable(v) else v
    except Exception:
        return default


def formatted_time(value):
    if isinstance(value, dt.datetime):
        # Store UTC ISO date when timezone info is available; otherwise use as-is.
        return value.isoformat(sep=' ', timespec='seconds')
    return safe_text(value)


def conn():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    return db


def prepare_db(db):
    db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER, path TEXT, route TEXT);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, folder_id INTEGER, message_index INTEGER,
            subject TEXT, sender TEXT, recipients TEXT, sent_time TEXT,
            body TEXT, attachments INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_messages_folder ON messages(folder_id,id);
        CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(sent_time);
    ''')
    # Simple substring search is more reliable for Chinese names/phrases.
    # Do not duplicate message text in a separate FTS index.
    return False


def pst_signature(path):
    st = path.stat()
    return json.dumps([str(path.resolve()), st.st_size, st.st_mtime_ns], ensure_ascii=False)


def is_indexed(path):
    if not DB.exists():
        return False
    with conn() as db:
        try:
            row = db.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
            done = db.execute("SELECT value FROM meta WHERE key='complete'").fetchone()
            return bool(row and done and row['value'] == pst_signature(path) and done['value'] == '1')
        except sqlite3.OperationalError:
            return False


def walk_index(folder, db, route, parent_id, label, fts_enabled):
    name = safe_text(prop(folder, 'name', '')) or label
    path = label if parent_id is None else label + '/' + name
    cur = db.execute('INSERT INTO folders(name,parent_id,path,route) VALUES(?,?,?,?)', (name, parent_id, path, json.dumps(route)))
    folder_id = cur.lastrowid
    count = int(prop(folder, 'number_of_sub_messages', 0) or 0)
    for j in range(count):
        try:
            m = folder.get_sub_message(j)
            subject = safe_text(prop(m, 'subject', ''))
            sender = safe_text(prop(m, 'sender_name', ''))
            recipients = safe_text(prop(m, 'display_to', '')) or safe_text(prop(m, 'received_by_name', ''))
            sent_time = formatted_time(prop(m, 'delivery_time') or prop(m, 'client_submit_time'))
            plain = prop(m, 'plain_text_body')
            # Only plain text is shown to avoid executing scripts and loading remote images.
            body = safe_text(plain)
            if not body:
                body = '[此邮件没有可读取的纯文本正文；可能只有 HTML 格式。]'
            acount = int(prop(m, 'number_of_attachments', 0) or 0)
            msg_id = db.execute('INSERT INTO messages(folder_id,message_index,subject,sender,recipients,sent_time,body,attachments) VALUES(?,?,?,?,?,?,?,?)',
                (folder_id,j,subject,sender,recipients,sent_time,body,acount)).lastrowid
            if fts_enabled:
                db.execute('INSERT INTO messages_fts(rowid,subject,sender,recipients,body) VALUES(?,?,?,?,?)', (msg_id,subject,sender,recipients,body))
            STATE['processed'] += 1
            if STATE['processed'] % 100 == 0:
                STATE['detail'] = f'{path} · 已处理 {STATE["processed"]:,} 封'
                db.commit()
        except Exception as exc:
            STATE['errors'] += 1
            if STATE['errors'] < 15:
                print(f'跳过损坏邮件 {path} #{j}: {exc}')
    db.commit()
    for i in range(int(prop(folder, 'number_of_sub_folders', 0) or 0)):
        try:
            sub = folder.get_sub_folder(i)
            # Full navigation routes use indices from PST root.
            walk_index(sub, db, route + [i], folder_id, path, fts_enabled)
        except Exception as exc:
            STATE['errors'] += 1
            print(f'跳过损坏文件夹 {path} #{i}: {exc}')


def index_worker(path):
    global PST
    STATE.update(status='indexing', processed=0, errors=0, detail='正在扫描 PST 文件夹', ready=False)
    try:
        with PST_LOCK:
            PST = pypff.file()
            PST.open(str(path))
            root = PST.get_root_folder()
            with conn() as db:
                fts_enabled = prepare_db(db)
                db.execute('DELETE FROM folders')
                db.execute('DELETE FROM messages')
                if fts_enabled:
                    db.execute('DELETE FROM messages_fts')
                db.execute('DELETE FROM meta')
                db.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('signature', pst_signature(path)))
                db.commit()
                walk_index(root, db, [], None, '邮箱', fts_enabled)
                db.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('complete', '1'))
                db.commit()
        STATE.update(status='ready', detail='索引建立完成', ready=True)
        print(f'索引完成：{STATE["processed"]:,} 封邮件，错误 {STATE["errors"]}')
    except Exception as exc:
        STATE.update(status='error', detail=f'{type(exc).__name__}: {exc}', ready=False)
        print('索引失败：', repr(exc))


def resolve_message(db, message_id):
    row = db.execute('''SELECT m.*, f.route, f.path AS folder_path
        FROM messages m JOIN folders f ON f.id=m.folder_id WHERE m.id=?''', (message_id,)).fetchone()
    if not row:
        raise LookupError('找不到邮件')
    with PST_LOCK:
        folder = PST.get_root_folder()
        for idx in json.loads(row['route']):
            folder = folder.get_sub_folder(idx)
        msg = folder.get_sub_message(row['message_index'])
        return row, msg


def attachments_info(message_id):
    with conn() as db:
        with PST_LOCK:
            row, m = resolve_message(db, message_id)
            result = []
            for i in range(int(prop(m, 'number_of_attachments', 0) or 0)):
                a = m.get_attachment(i)
                name = safe_text(prop(a, 'long_filename')) or safe_text(prop(a, 'name')) or f'附件_{i+1}.bin'
                result.append({'index': i, 'name': name, 'size': prop(a, 'size', 0)})
            return result



def html_mail_document(raw):
    """Return an isolated HTML document; CSP prevents external loads and scripts.
    Content is rendered only inside sandboxed iframe, never in app DOM.
    """
    data = safe_text(raw)
    if len(data) > 12_000_000:
        data = data[:12_000_000] + '<p>[正文过长，已截断预览]</p>'
    csp = ("default-src 'none'; script-src 'none'; connect-src 'none'; "
           "img-src data:; style-src 'unsafe-inline'; font-src data:; "
           "frame-src 'none'; object-src 'none'; form-action 'none'; "
           "base-uri 'none'")
    # CSP meta must precede email markup (including any email-provided head).
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="' + html.escape(csp, quote=True) + '">'
            '<style>body{overflow-wrap:anywhere;line-height:1.5;font-family:Arial,sans-serif;margin:12px}'
            'table{max-width:100%}img{max-width:100%;height:auto}</style></head><body>'
            + data + '</body></html>')


PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地 PST 邮件查看器</title><style>
:root{color-scheme:light;font-family:system-ui,"Microsoft YaHei",sans-serif;color:#18283f;background:#f4f7fb}
*{box-sizing:border-box}body{margin:0}header{background:#fff;border-bottom:1px solid #e5eaf1;padding:18px 25px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}
header strong{font-size:20px}small,.muted{color:#718096}.layout{display:grid;grid-template-columns:240px 365px minmax(300px,1fr);height:calc(100vh - 85px)}
aside,.list,.viewer{overflow:auto;background:#fff}aside,.list{border-right:1px solid #e8edf3}
.pane-head{padding:15px;border-bottom:1px solid #e7ecf2;position:sticky;top:0;background:#fff;z-index:3}
input{width:100%;padding:11px;border:1px solid #ccd6e2;border-radius:9px;font:inherit}
button{font:inherit;cursor:pointer;border:0;border-radius:7px;padding:8px 10px;background:#eaf1fd;color:#2652a0}
.folder{display:block;width:100%;text-align:left;background:transparent;padding:10px 14px;border-radius:0;overflow-wrap:anywhere}
.folder:hover,.item:hover{background:#f2f6fc}.folder.active,.item.active{background:#e8f1ff}
.item{text-align:left;width:100%;display:block;background:#fff;border-radius:0;padding:15px;border-bottom:1px solid #eef0f5}
.subject{font-weight:650;overflow-wrap:anywhere}.meta{font-size:12px;color:#748198;margin-top:5px;overflow-wrap:anywhere}
.viewer{padding:28px;min-width:0}.body{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.75;font-size:14px;border-top:1px solid #e9edf3;padding-top:18px;margin-top:18px}
h2{overflow-wrap:anywhere}.htmlview{border:1px solid #e5eaf1;border-radius:8px;width:100%;min-height:520px;height:70vh;background:white;margin-top:14px}.viewtools{display:flex;gap:8px;margin-top:18px}.pill{background:#f4f7fb;border-radius:9px;padding:12px;margin-top:9px;overflow-wrap:anywhere}
a{color:#2761bd}.status{font-size:13px;color:#55677f}.toolbar{display:flex;gap:8px;align-items:center;margin-top:10px}
@media(max-width:900px){.layout{grid-template-columns:180px 270px minmax(260px,1fr)}}
</style></head><body><header><strong>✉ 本地 PST 邮件查看器 V2.1</strong><span class="status" id="status">正在启动…</span><small>仅监听本机 127.0.0.1 · 不会上传邮件</small></header>
<div class="layout"><aside><div class="pane-head"><b>邮件文件夹</b><div class="meta" id="folderinfo"></div></div><div id="folders"></div></aside>
<div class="list"><div class="pane-head"><input id="search" placeholder="搜索主题、发件人或正文（按回车）"><div class="toolbar"><button id="searchbtn">搜索</button><button id="clearbtn">清除</button><span class="meta" id="count"></span></div></div><div id="items"></div><div class="pane-head"><button id="more" style="display:none">加载更多</button></div></div>
<div class="viewer" id="detail"><h2>选择一封邮件</h2><p class="muted">首次使用会自动建立索引。可以搜索学校邮件，也可以按文件夹浏览。</p><p class="muted">为保护隐私，正文可切换纯文本与安全隔离的 HTML 正文；禁止外部资源加载。</p></div></div>
<script>
let folderId='', query='', offset=0, active=null, busy=false;
const el=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url){const r=await fetch(url);let v=await r.json();if(!r.ok)throw Error(v.error||r.status);return v;}
async function status(){try{const d=await api('/api/status');el('status').textContent=d.ready?`已就绪 · ${d.processed.toLocaleString()} 封邮件 · ${d.errors} 处读取问题`: `${d.status} · ${d.detail} · ${d.processed.toLocaleString()} 封`;
if(d.ready&&!window.initialized){window.initialized=true;await folders();await list(true);}if(d.status==='error')el('detail').textContent='无法读取 PST：'+d.detail;
if(!d.ready)setTimeout(status,1300);}catch(e){el('status').textContent=e.message;setTimeout(status,2000);}}
async function folders(){const data=await api('/api/folders');el('folders').replaceChildren();
const all=document.createElement('button');all.className='folder active';all.textContent='📨 全部邮件';all.onclick=()=>selectFolder('',all);el('folders').append(all);
for(const f of data){const b=document.createElement('button');b.className='folder';const depth=Math.min(f.depth,5);b.style.paddingLeft=(14+depth*14)+'px';b.textContent='📁 '+f.name+' ('+f.count+')';b.title=f.path;b.onclick=()=>selectFolder(f.id,b);el('folders').append(b);}}
function selectFolder(id,button){folderId=id;document.querySelectorAll('.folder').forEach(x=>x.classList.remove('active'));button.classList.add('active');list(true);}
async function list(reset){if(busy)return;busy=true;try{if(reset){offset=0;el('items').replaceChildren();el('detail').innerHTML='<h2>选择一封邮件</h2>';}
const d=await api('/api/messages?folder='+encodeURIComponent(folderId)+'&q='+encodeURIComponent(query)+'&offset='+offset);
el('count').textContent=d.total.toLocaleString()+' 封';for(const m of d.items){const b=document.createElement('button');b.className='item';b.innerHTML='<div class="subject">'+esc(m.subject||'(无主题)')+'</div><div class="meta">'+esc(m.sender)+'</div><div class="meta">'+esc(m.sent_time||'未知日期')+(m.attachments?' · 📎':'')+'</div>';b.onclick=()=>openMail(m.id,b);el('items').append(b);}
offset+=d.items.length;el('more').style.display=offset<d.total?'inline-block':'none';}catch(e){alert('读取列表失败：'+e.message)}finally{busy=false;}}
async function openMail(id,b){document.querySelectorAll('.item').forEach(x=>x.classList.remove('active'));b.classList.add('active');try{
const d=await api('/api/message?id='+id);const box=el('detail');box.replaceChildren();const h=document.createElement('h2');h.textContent=d.subject||'(无主题)';box.append(h);
for(const [k,v] of [['发件人',d.sender],['收件人',d.recipients],['时间',d.sent_time],['文件夹',d.folder_path]]){const p=document.createElement('div');p.className='pill';p.textContent=k+'：'+(v||'未知');box.append(p)}
if(d.attachments){const title=document.createElement('h3');title.textContent='附件';box.append(title);try{const atts=await api('/api/attachments?id='+id);atts.forEach(a=>{const p=document.createElement('div');p.className='pill';const link=document.createElement('a');link.textContent='📎 '+a.name+' ('+Math.round((a.size||0)/1024)+' KB)';link.href='/api/download?id='+id+'&index='+a.index;p.append(link);box.append(p)})}catch(e){box.append(document.createTextNode('无法读取附件列表：'+e.message));}}
const viewbar=document.createElement('div');viewbar.className='viewtools';
const htmlbtn=document.createElement('button');htmlbtn.textContent='HTML 原始排版';
const plainbtn=document.createElement('button');plainbtn.textContent='纯文本';
viewbar.append(htmlbtn,plainbtn);box.append(viewbar);
const content=document.createElement('div');content.className='body';content.textContent=d.body||'[无纯文本正文]';box.append(content);
const frame=document.createElement('iframe');frame.className='htmlview';frame.setAttribute('sandbox','');frame.setAttribute('referrerpolicy','no-referrer');frame.style.display='none';box.append(frame);
const note=document.createElement('p');note.className='meta';note.textContent='安全提示：HTML 邮件在隔离窗口中显示，禁用脚本、外部图片与网络连接。内嵌 CID 图片可能无法显示。';box.append(note);
plainbtn.onclick=()=>{frame.style.display='none';content.style.display='block';};
htmlbtn.onclick=async()=>{try{if(!frame.dataset.loaded){htmlbtn.disabled=true;htmlbtn.textContent='正在读取 HTML…';const h=await api('/api/html?id='+id);frame.srcdoc=h.document;frame.dataset.loaded='1';htmlbtn.disabled=false;htmlbtn.textContent='HTML 原始排版';}content.style.display='none';frame.style.display='block';}catch(e){htmlbtn.disabled=false;htmlbtn.textContent='HTML 原始排版';alert('读取 HTML 失败：'+e.message)}};
if(d.body&&d.body.startsWith('[此邮件没有可读取的纯文本正文'))htmlbtn.click();
box.scrollTop=0;
}catch(e){alert('打开邮件失败：'+e.message)}}
function doSearch(){query=el('search').value.trim();list(true)}el('searchbtn').onclick=doSearch;el('search').onkeydown=e=>{if(e.key==='Enter')doSearch()};el('clearbtn').onclick=()=>{query='';el('search').value='';list(true)};el('more').onclick=()=>list(false);status();
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Avoid logging search queries to the terminal.
        return

    def json_response(self, data, status=200):
        payload=json.dumps(data,ensure_ascii=False,default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        host = self.headers.get('Host', '').split(':', 1)[0].strip().lower()
        if host not in ('127.0.0.1', 'localhost', '[::1]'):
            self.json_response({'error':'仅允许本机访问'}, 403)
            return
        parts=urllib.parse.urlsplit(self.path)
        args=urllib.parse.parse_qs(parts.query)
        get=lambda key,default='':args.get(key,[default])[0]
        try:
            if parts.path=='/':
                payload=PAGE.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers();self.wfile.write(payload);return
            if parts.path=='/api/status':
                self.json_response(STATE);return
            if not STATE['ready']:
                self.json_response({'error':'邮件索引尚未完成，请稍后刷新'},503);return
            if parts.path=='/api/folders':
                with conn() as db:
                    rows=db.execute('''SELECT f.id,f.name,f.path,f.parent_id, count(m.id) AS count
                        FROM folders f LEFT JOIN messages m ON m.folder_id=f.id GROUP BY f.id ORDER BY f.id''').fetchall()
                    folders=[dict(r) for r in rows]
                    ids={f['id']:f for f in folders}
                    for f in folders:
                        depth=0;p=f['parent_id']
                        while p is not None and depth<25:
                            depth+=1;p=ids.get(p,{}).get('parent_id')
                        f['depth']=depth
                    self.json_response(folders);return
            if parts.path=='/api/messages':
                folder=get('folder');q=get('q').strip();offset=max(0,min(int(get('offset','0')),100000000))
                where=[];pars=[]
                if folder:
                    where.append('m.folder_id=?');pars.append(int(folder))
                if q:
                    # LIKE fallback supports substrings, quotes and CJK without tokenization issues.
                    escaped=q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                    where.append("(m.subject LIKE ? ESCAPE '\\' OR m.sender LIKE ? ESCAPE '\\' OR m.recipients LIKE ? ESCAPE '\\' OR m.body LIKE ? ESCAPE '\\')")
                    pars.extend(['%'+escaped+'%']*4)
                sql=' WHERE '+' AND '.join(where) if where else ''
                with conn() as db:
                    total=db.execute('SELECT count(*) FROM messages m'+sql,pars).fetchone()[0]
                    rows=db.execute('SELECT m.id,m.subject,m.sender,m.sent_time,m.attachments FROM messages m'+sql+' ORDER BY m.sent_time DESC,m.id DESC LIMIT 60 OFFSET ?',pars+[offset]).fetchall()
                self.json_response({'total':total,'items':[dict(r) for r in rows]});return
            if parts.path=='/api/message':
                with conn() as db:
                    row=db.execute('''SELECT m.*,f.path AS folder_path FROM messages m JOIN folders f ON f.id=m.folder_id WHERE m.id=?''',(int(get('id')),)).fetchone()
                if not row:self.json_response({'error':'邮件不存在'},404);return
                self.json_response(dict(row));return
            if parts.path=='/api/html':
                message_id=int(get('id'))
                with conn() as db:
                    with PST_LOCK:
                        row, m = resolve_message(db, message_id)
                        body = prop(m,'html_body')
                        if not body:
                            self.json_response({'error':'这封邮件没有 HTML 正文'},404);return
                        doc = html_mail_document(body)
                self.json_response({'document':doc});return
            if parts.path=='/api/attachments':
                self.json_response(attachments_info(int(get('id'))));return
            if parts.path=='/api/download':
                message_id=int(get('id'));idx=int(get('index'))
                with conn() as db:
                    with PST_LOCK:
                        row,msg=resolve_message(db,message_id)
                        n=int(prop(msg,'number_of_attachments',0) or 0)
                        if idx<0 or idx>=n:raise LookupError('附件不存在')
                        attachment=msg.get_attachment(idx)
                        name=safe_text(prop(attachment,'long_filename')) or safe_text(prop(attachment,'name')) or f'附件_{idx+1}.bin'
                        name=re.sub(r'[\\/\r\n\x00-\x1f]','_',name)[:180]
                        size=int(prop(attachment,'size',0) or 0)
                        attachment.seek_offset(0,0)
                        self.send_response(200)
                        self.send_header('Content-Type','application/octet-stream')
                        self.send_header('X-Content-Type-Options','nosniff')
                        self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(name))
                        self.send_header('Cache-Control','no-store')
                        if size:self.send_header('Content-Length',str(size))
                        self.end_headers()
                        left=size
                        while left>0:
                            data=attachment.read_buffer(min(left,1024*1024))
                            if not data:break
                            self.wfile.write(data)
                            left-=len(data)
                return
            self.json_response({'error':'接口不存在'},404)
        except (ValueError,LookupError) as exc:
            self.json_response({'error':str(exc)},400)
        except (BrokenPipeError,ConnectionResetError):
            return
        except Exception as exc:
            try:self.json_response({'error':f'{type(exc).__name__}: {exc}'},500)
            except Exception:pass


def main():
    global PST
    parser=argparse.ArgumentParser(description='离线本地 PST 邮件查看器')
    parser.add_argument('pst',help='PST 文件绝对路径')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    path=Path(args.pst).expanduser().resolve()
    if not path.is_file():
        print('找不到 PST 文件：',path);sys.exit(2)
    if is_indexed(path):
        STATE.update(status='ready',ready=True,detail='已使用上次的邮件索引')
        with conn() as db:
            STATE['processed']=db.execute('SELECT count(*) FROM messages').fetchone()[0]
        PST=pypff.file();PST.open(str(path))
    else:
        threading.Thread(target=index_worker,args=(path,),daemon=True).start()
    url=f'http://127.0.0.1:{args.port}'
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print('本地查看器：',url)
    print('退出请按 Ctrl+C；仅在这台电脑上访问。')
    threading.Timer(1.0, lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:print('正在退出…')
    finally:
        server.server_close()
        if PST is not None:
            with PST_LOCK:
                PST.close()

if __name__=='__main__':main()

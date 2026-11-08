# -*- coding: utf-8 -*-
"""Local WeChat Channels downloader app: paste a share link, download the video."""

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
DOWNLOAD_DIR = ROOT / "downloads"
CONFIG = ROOT / "config.api.yaml"
CONFIG_EXAMPLE = ROOT / "config.example.yaml"
EXE = ROOT / "wx_video_download_debug.exe"
if not EXE.exists() and (ROOT / "wx_video_download.exe").exists():
    EXE = ROOT / "wx_video_download.exe"
INDEX = ROOT / "index.html"
APP_PORT = 8765
API_PORT = 2022
API_BASE = f"http://127.0.0.1:{API_PORT}"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TASKS = []
TASK_LOCK = threading.Lock()
QUEUE = queue.Queue()
NEXT_ID = [1]


def http_get(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with OPENER.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def api_alive():
    try:
        http_get(f"{API_BASE}/api/status", timeout=2)
        return True
    except Exception:
        return False


def _find_sph_ready(obj):
    if isinstance(obj, dict):
        if obj.get("key") == "wxchannels:sph" and obj.get("available") is True:
            return True
        return any(_find_sph_ready(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_find_sph_ready(v) for v in obj)
    return False


def parse_ready():
    try:
        return _find_sph_ready(json.loads(http_get(f"{API_BASE}/api/scraper/platform/status", timeout=4)))
    except Exception:
        return False


def get_cookie():
    try:
        text = CONFIG.read_text(encoding="utf-8")
        m = re.search(r"^\s*sphCookie:\s*'(.*)'\s*$", text, re.M)
        return m.group(1).replace("''", "'") if m else ""
    except Exception:
        return ""


def save_cookie(cookie):
    cookie = cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[7:].strip()
    escaped = cookie.replace("'", "''")
    try:
        text = CONFIG.read_text(encoding="utf-8")
    except Exception:
        text = "proxy:\n  enabled: false\n  system: false\n  tun: false\ncloudflare:\n"
    if re.search(r"^\s*sphCookie:", text, re.M):
        text = re.sub(r"^(\s*sphCookie:\s*).*$", lambda m: m.group(1) + "'" + escaped + "'", text, flags=re.M)
    elif re.search(r"^cloudflare:\s*$", text, re.M):
        text = re.sub(r"^cloudflare:\s*$", "cloudflare:\n  sphCookie: '" + escaped + "'", text, flags=re.M)
    else:
        text = text.rstrip() + "\ncloudflare:\n  sphCookie: '" + escaped + "'\n"
    CONFIG.write_text(text, encoding="utf-8")


def kill_downloader():
    for name in ("wx_video_download_debug.exe", "wx_video_download.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)


def ensure_ready(force=False):
    if not force and api_alive() and parse_ready():
        return True
    kill_downloader()
    log = ROOT / "app_downloader.log"
    with log.open("ab") as f:
        subprocess.Popen([str(EXE), "-c", str(CONFIG)], cwd=str(ROOT), stdout=f,
                         stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
    for _ in range(30):
        time.sleep(0.5)
        if api_alive():
            return True
    return False


def resolve(url):
    """Return (video_url, description, author, kind)."""
    if "weixin.qq.com/sph/" in url or "channels.weixin.qq.com" in url:
        api = f"{API_BASE}/api/channels/parse_sph?url={urllib.parse.quote(url, safe='')}"
        data = json.loads(http_get(api, timeout=45))
        if data.get("code") != 0:
            raise RuntimeError(data.get("msg") or "链接解析失败（Cookie 可能已过期）")
        inner = data.get("data", {}).get("data", {})
        feed = inner.get("feedInfo", {})
        video_url = feed.get("videoUrl") or feed.get("h264VideoInfo", {}).get("videoUrl", "")
        if not video_url:
            raise RuntimeError("解析结果中没有视频地址")
        author = inner.get("authorInfo", {}).get("nickname", "")
        return video_url, feed.get("description", ""), author, "视频号"
    if url.startswith("http://") or url.startswith("https://"):
        return url, "", "", "直链"
    raise RuntimeError("不支持的链接，请粘贴 https://weixin.qq.com/sph/... 或视频直链")


def probe_total(url):
    try:
        req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "Mozilla/5.0"})
        with OPENER.open(req, timeout=20) as resp:
            cr = resp.headers.get("Content-Range", "")
            if "/" in cr:
                return int(cr.rsplit("/", 1)[1])
            cl = resp.headers.get("Content-Length", "")
            return int(cl) if cl.isdigit() else 0
    except Exception:
        return 0


def safe_name(text):
    text = re.sub(r'[#"\\/:*?<>|\r\n\t]', "", str(text or "")).strip()
    text = re.sub(r"\s+", "_", text)
    return text[:40]


def build_filename(description, author, kind, url):
    if description:
        base = safe_name(description)
        if author:
            base += "_" + safe_name(author)
    elif kind == "直链":
        base = "video_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    else:
        base = "wxchannels_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    if not base:
        base = "video_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    return base + ".mp4"


def update(task, **kw):
    with TASK_LOCK:
        task.update(kw)
        if task.get("total"):
            task["progress"] = min(100, round(task.get("downloaded", 0) * 100 / task["total"], 1))


def process(task):
    try:
        update(task, status="parsing", message="解析链接中")
        video_url, desc, author, kind = resolve(task["url"])
        update(task, title=desc or task["url"][-24:], author=author, kind=kind)
        total = probe_total(video_url)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        out = DOWNLOAD_DIR / build_filename(desc, author, kind, task["url"])
        update(task, file=str(out), total=total, downloaded=out.stat().st_size if out.exists() else 0)
        if total and out.exists() and out.stat().st_size >= total:
            update(task, status="done", message="文件已存在，跳过下载", progress=100)
            return
        update(task, status="downloading", message="下载中")
        code = -1
        for attempt in range(8):
            cmd = ["curl.exe", "-q", "-sS", "--noproxy", "*", "--connect-timeout", "6",
                   "--max-time", "300", "-C", "-", "-o", str(out), "-w", "%{http_code}", video_url]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=CREATE_NO_WINDOW)
            while proc.poll() is None:
                size = out.stat().st_size if out.exists() else 0
                update(task, downloaded=size, message=f"下载中（第 {attempt + 1} 次）")
                time.sleep(0.4)
            _, err = proc.communicate()
            code = proc.returncode
            size = out.stat().st_size if out.exists() else 0
            update(task, downloaded=size)
            if code == 0 and (total == 0 or size >= total):
                break
            time.sleep(1.5)
        if total and (not out.exists() or out.stat().st_size < total):
            raise RuntimeError(f"下载不完整：{out.stat().st_size if out.exists() else 0} / {total} 字节")
        if code != 0 and total == 0:
            raise RuntimeError((err or b"").decode("utf-8", "replace").strip() or "下载失败")
        update(task, status="done", message="下载完成", progress=100,
               downloaded=out.stat().st_size if out.exists() else 0)
    except Exception as exc:
        update(task, status="error", message=str(exc))


def worker():
    while True:
        task = QUEUE.get()
        process(task)
        QUEUE.task_done()


def status_payload():
    alive = api_alive()
    return {
        "api": alive,
        "parse": parse_ready() if alive else False,
        "cookie": bool(get_cookie()),
        "dir": str(DOWNLOAD_DIR),
        "queued": QUEUE.qsize(),
    }


def list_files():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(DOWNLOAD_DIR.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        st = p.stat()
        items.append({"name": p.name, "size": st.st_size, "path": str(p),
                      "time": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    return items


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/status":
                self._json(status_payload())
            elif path == "/api/tasks":
                with TASK_LOCK:
                    self._json({"tasks": [dict(t) for t in TASKS]})
            elif path == "/api/files":
                self._json({"files": list_files()})
            elif path == "/favicon.ico":
                self._send(204, b"")
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
            body = json.loads(raw or "{}")
            if path == "/api/download":
                urls = body.get("urls") or ([body["url"]] if body.get("url") else [])
                urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
                if not urls:
                    return self._json({"error": "请粘贴链接"}, 400)
                ids = []
                with TASK_LOCK:
                    for u in urls:
                        task = {"id": NEXT_ID[0], "url": u, "title": u[-28:], "author": "",
                                "kind": "", "status": "queued", "message": "排队中", "progress": 0,
                                "downloaded": 0, "total": 0, "file": ""}
                        NEXT_ID[0] += 1
                        TASKS.insert(0, task)
                        if len(TASKS) > 60:
                            TASKS.pop()
                        ids.append(task["id"])
                        QUEUE.put(task)
                self._json({"ok": True, "ids": ids})
            elif path == "/api/cookie":
                cookie = body.get("cookie", "")
                if not cookie.strip():
                    return self._json({"error": "Cookie 不能为空"}, 400)
                save_cookie(cookie)
                ready = ensure_ready(force=True)
                self._json({"ok": True, "ready": ready, "status": status_payload()})
            elif path == "/api/restart":
                self._json({"ok": True, "ready": ensure_ready(force=True), "status": status_payload()})
            elif path == "/api/open":
                target = Path(body.get("path", ""))
                folder = target.parent.resolve() if target.name else target.resolve()
                allowed = DOWNLOAD_DIR.resolve()
                if folder != allowed and target.resolve() != allowed:
                    return self._json({"error": "路径不在下载目录内"}, 400)
                os.startfile(str(target.resolve()))
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


def port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def main():
    if port_in_use(APP_PORT):
        webbrowser.open(f"http://127.0.0.1:{APP_PORT}/")
        print("App already running, opened browser.")
        return
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists() and CONFIG_EXAMPLE.exists():
        CONFIG.write_text(CONFIG_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    threading.Thread(target=worker, daemon=True).start()
    print("Starting downloader service...")
    ensure_ready()
    server = ThreadingHTTPServer(("127.0.0.1", APP_PORT), Handler)
    print(f"WXVideoDown app ready: http://127.0.0.1:{APP_PORT}/")
    webbrowser.open(f"http://127.0.0.1:{APP_PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

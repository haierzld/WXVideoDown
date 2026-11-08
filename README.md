# WXVideoDown · 本地视频号下载小应用

粘贴微信视频号分享链接就能下载视频的本地小工具。Windows + Python 3，只监听 `127.0.0.1`，不依赖微信页面上的下载按钮。

## 为什么需要它

微信 4.2 起，PC 端视频号改用 LiteApp 渲染，页面不再请求传统的 `channels.weixin.qq.com` HTML，上游项目「向页面注入下载按钮」的做法在新版微信里不生效（页面里不会出现按钮）。

本应用换了一条路：让上游服务以**纯 API 模式**运行，调用 `/api/channels/parse_sph` 把分享短链解析成 `feedInfo.videoUrl`（明文 H264 MP4，响应头 `X-encflag: 0`），再直接下载，不需要解密、不需要页面注入、不需要微信重启、不修改系统代理。

## 功能

- 粘贴即下载：支持 `https://weixin.qq.com/sph/...` 分享链接，可一次粘贴多行批量下载
- 也支持 `finder.video.qq.com` 等视频直链
- 实时进度条、断点续传（CDN 断连自动续传重试）、已存在的完整文件自动跳过
- 按「标题_作者.mp4」命名，统一存到 `downloads/`
- 网页里可直接更新 Cookie、重启服务、打开视频或下载文件夹
- 不需要管理员权限，不改系统代理，不动微信登录状态

## 准备

1. **下载器主程序**：从上游项目 [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download) 的 Release 获取 `wx_video_download.exe`（或用 Go 自行编译），放到本目录。本仓库不包含二进制文件；debug 版 `wx_video_download_debug.exe` 同样可用，应用会自动识别。
2. **Python 3.8+**：本应用只用标准库，无需 `pip install`。
3. **curl.exe**：Windows 10/11 自带，用于下载视频。

## 启动

双击 `start.bat`，或在目录里执行：

```bash
python app.py
```

浏览器会自动打开 `http://127.0.0.1:8765/`。首次运行会把 `config.example.yaml` 复制成本地配置 `config.api.yaml`（已被 `.gitignore` 排除）。重复双击启动脚本不会冲突，只会打开已运行的页面。

## 如何获得 Cookie（关键步骤）

解析分享链接需要 `yuanbao.tencent.com` 的**登录 Cookie**，有效期约 1 个月。

1. 用 Chrome 或 Edge 打开 <https://yuanbao.tencent.com>，用微信或 QQ 扫码登录。
2. 按 `F12` 打开开发者工具，切到 **Network（网络）** 面板，然后刷新页面。
3. 在请求列表里任选一个 `yuanbao.tencent.com` 的请求（建议选 XHR/Fetch 类型，例如任意 `api/...` 请求）。
4. 点开该请求的 **Headers → Request Headers**，找到 `Cookie` 这一行，右键选择「Copy value / 复制值」。
   - 要的是 `key=value; key=value; ...` 那一整串值。
   - 如果复制成了 `Cookie: xxxx` 整行也没关系，应用会自动去掉 `Cookie:` 前缀。
   - 不要在 Application 面板里逐条复制单个 cookie，必须是完整的请求头值。
5. 打开本地应用页面 → 展开「设置与维护」→ 把整串粘贴进输入框 → 点「**保存并重启服务**」。
   顶部「Cookie」「解析」两个徽章变绿后，就可以粘贴链接下载了。
   也可以直接编辑 `config.api.yaml`：

   ```yaml
   cloudflare:
     sphCookie: '这里粘贴完整 Cookie'
   ```

6. **过期表现**：任务提示「链接解析失败（Cookie 可能已过期）」，或解析接口返回错误。按上面 1–5 重新获取一次即可，页面里更新不用改文件。

### 安全提醒

Cookie 等同于账号登录凭据：

- 只保存在本机 `config.api.yaml`，页面不会回显，也不会上传到任何地方；
- 仓库的 `.gitignore` 已排除 `config.api.yaml`、`downloads/`、exe 与日志文件；
- 不要发到聊天群、截图、贴进 issue 或提交到公开仓库；若已泄露，到元宝网页退出登录使其失效。

## 使用

1. 在微信视频号里点「分享 → 复制链接」，得到 `https://weixin.qq.com/sph/...`。
2. 在页面输入框 `Ctrl+V`（或点「从剪贴板粘贴」），可一次粘贴多行。
3. 点「开始下载」或 `Ctrl+Enter`；任务卡片会显示标题、作者、大小与进度条。
4. 完成后点「打开视频」，或「打开下载文件夹」。

命令行备选（不启界面）：

```powershell
powershell -ExecutionPolicy Bypass -File .\download-by-link.ps1 -Url "https://weixin.qq.com/sph/xxxxx"
```

## 常见问题

- **顶部「服务 / 解析」徽章异常**：点「仅重启服务」；仍不行查看 `app_downloader.log`。
- **下载中途失败**：内置 8 次断点续传重试，一般会自动补齐；`downloads/` 里的残缺文件会被续写而不是重新下载。
- **想换清晰度**：修改 `app.py` 的 `resolve()`，把 `feedInfo.videoUrl` 换成 `feedInfo.h264VideoInfo.videoUrl` 或 `feedInfo.h265VideoInfo.videoUrl`。
- **端口 8765 被占用**：应用已在运行，直接打开 `http://127.0.0.1:8765/` 即可。

## 原理与致谢

上游项目 [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download) 提供了 `/api/channels/parse_sph` 接口和 `proxy.enabled: false` 的纯 API 运行模式。本应用只做本地 Web 界面、任务队列、下载与 Cookie 管理，不修改上游源码。

使用请遵循上游项目许可证与平台使用条款，仅下载自己有权保存的内容。

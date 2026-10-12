# `wx_channels_download` 视频号下载机制与 BiliSum 接入研究

研究日期：2026-09-02  
目标仓库：[`ltaoo/wx_channels_download`](https://github.com/ltaoo/wx_channels_download)  
审计提交/最新发布：[`2bcc78bd1641985405f362226a9dc9b50eb2b3cd`](https://github.com/ltaoo/wx_channels_download/tree/2bcc78bd1641985405f362226a9dc9b50eb2b3cd)，`v260830`

## 结论

该仓库能够下载用户给出的 `https://weixin.qq.com/sph/AcPb6XFo5S` 这类视频号分享链接，但它不是一个只靠公开分享页 HTML 就能工作的普通下载器。

它有两条发现视频详情的路径：

1. **分享链接直解析**：把 `weixin.qq.com/sph/...` 发给腾讯元宝解析接口，取得可播放页面中的 `token` 和 `eid`，再调用视频号 `get_feed_info` 接口拿到媒体 URL。该路径优先使用本机保存的 `yuanbao.tencent.com` Cookie（[`client.go#L75-L121`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/client.go#L75-L121)，[`yuanbao.go#L104-L163`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/yuanbao.go#L104-L163)，[`yuanbao.go#L166-L247`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/yuanbao.go#L166-L247)）。
2. **微信页面回退**：没有元宝 Cookie 时，程序把分享 URL 通过本地 WebSocket 发给已打开、已注入脚本的视频号页面，让微信页面自身调用接口解析。若没有已连接的视频号页面，该路径会报“请先初始化 client socket connection”（[`client.go#L92-L97`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/client.go#L92-L97)，[`client.go#L241-L304`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/client.go#L241-L304)）。

因此，“仅凭分享链接”在接口形态上可行，但运行时仍需满足至少一个条件：有可用的元宝 Cookie，或微信 PC 客户端中有一个经本机代理注入并已连上 WebSocket 的视频号页面。它不等同于匿名、无状态的服务器解析。

## 最短可操作流程（Windows）

1. 从项目的 [GitHub Releases](https://github.com/ltaoo/wx_channels_download/releases/tag/v260830) 下载 `v260830` 的 `windows_x86_64` ZIP。若常规包被杀毒软件误报，选择文件名带 `safe` 的未 UPX 压缩包；构建配置明确区分了普通包与 `windows_plain`/`safe` 包（[`goreleaser.yaml#L15-L92`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/build/goreleaser.yaml#L15-L92)）。不要从第三方网盘获取。
2. 解压完整 ZIP。默认 `config.yaml` 使用本机 API `127.0.0.1:2022`、HTTP 代理 `127.0.0.1:2023`、系统代理开启、下载目录为用户 Downloads（[`config.template.yaml#L7-L25`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/config/config.template.yaml#L7-L25)，[`config.template.yaml#L42-L54`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/config/config.template.yaml#L42-L54)）。
3. 首次右键 `wx_video_download.exe`，选择“以管理员身份运行”。程序需要把 CA 安装到 `LocalMachine\\Root`，Windows 实现调用 `certutil -addstore Root`（[`certificate_windows.go#L68-L86`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/certificate/certificate_windows.go#L68-L86)）。后续若证书已安装且不使用 TUN，通常不必一直管理员运行。
4. 等终端出现代理服务启动成功。程序会把当前用户的 Windows 系统代理指向 `127.0.0.1:2023`（[`proxy_windows.go#L23-L35`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/system/proxy_windows.go#L23-L35)）。
5. 完全退出并重开微信 PC 客户端，打开视频号，再打开目标分享链接或在视频号中找到目标视频。
6. 等视频开始播放，然后暂停。仓库 README 明确要求这一步；页面下方、侧边或底部会出现注入的“下载”按钮（[`README#L5-L31`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/README.md#L5-L31)）。
7. 点击“下载”获取当前播放规格；下拉菜单可选其他规格。默认下载目录是 Windows Downloads，可在 `config.yaml` 的 `download.dir` 改为绝对路径；默认最多并发 3 个任务（[`download.md#L29-L61`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/docs/config/download.md#L29-L61)）。
8. 正常退出下载器。停止逻辑只在系统代理仍指向本程序时将其关闭，避免覆盖用户后来切换的代理（[`interceptor.go#L115-L132`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/interceptor/interceptor.go#L115-L132)）。若异常崩溃后断网，手动关闭 Windows“使用代理服务器”或恢复原代理。

从源码运行需要 Go 1.20；README 给出的开发命令是在管理员终端执行 `go run main.go`（[`go.mod#L1-L3`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/go.mod#L1-L3)，[`README#L34-L39`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/README.md#L34-L39)）。本次研究没有运行仓库发布的二进制。

## 完整数据流

```text
微信 PC 视频号 HTTPS 请求
        |
Windows 系统代理 127.0.0.1:2023
        |
本地 TLS 中间人代理，使用受信任 CA 动态签发站点证书
        |
拦截 channels.weixin.qq.com HTML/JS 响应并注入脚本
        |
注入脚本观察页面 feed、媒体规格、URL、decodeKey
        |
WebSocket /ws/channels <-> 本机 API 127.0.0.1:2022
        |
创建后台下载任务 -> 下载 CDN 字节 -> 按需解密前 128 KiB
        |
写入 Downloads（可选封面、MP3、图集或直播后处理）
```

- 代理只对匹配插件做内容拦截；`channels.weixin.qq.com` 的 HTML 响应会被读取、改写并插入本地脚本，微信原有 JS 也会按正则打补丁以暴露 feed 事件和接口（[`interceptor.go#L70-L118`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/interceptor.go#L70-L118)，[`interceptor.go#L163-L199`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/interceptor.go#L163-L199)）。
- 注入后的页面连接 `/ws/channels`；后端经它请求视频详情，10 秒未返回即超时（[`routes.go#L42-L75`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/adapter/wxchannels/routes.go#L42-L75)，[`client.go#L253-L304`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/client.go#L253-L304)）。
- 页面按钮由注入脚本绑定到下载处理器；详情页按钮、悬浮兜底按钮和 feed 切换监听均在注入脚本中（[`channels.feed.js#L9-L83`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/inject/channels.feed.js#L9-L83)，[`channels.feed.js#L173-L207`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/inject/channels.feed.js#L173-L207)）。
- 后端将媒体 URL、标题、规格和 `decode_key` 建成持久下载任务；视频资源使用 HTTPS URL，下载后由平台后处理器处理（[`model.go#L623-L625`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/adapter/wxchannels/model.go#L623-L625)，[`model.go#L750-L800`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/adapter/wxchannels/model.go#L750-L800)）。

## 解密与密钥

正常 GUI 流程**涉及解密，但不要求用户手工找密钥**。视频详情数据的媒体项包含 `decodeKey`，程序把它随下载资源保存；只有存在该值时才走解密节点。

实现使用 64 位 ISAAC 伪随机序列与密文字节异或，只处理前 `131072` 字节（128 KiB），不是 DRM 许可证交换。算法入口见 [`decrypt.go#L18-L46`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/decrypt.go#L18-L46) 与 [`decrypt.go#L149-L168`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/decrypt.go#L149-L168)；下载后原地替换文件的后处理见 [`postprocess.go#L2169-L2195`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/adapter/wxchannels/postprocess.go#L2169-L2195)。

普通 MP4 下载和解密本身不依赖 ffmpeg；转换 MP3、部分直播流整理会执行系统 `ffmpeg`/`ffprobe`（[`decryptor.go#L37-L85`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/decryptor.go#L37-L85)，[`postprocess.go#L2325-L2387`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/adapter/wxchannels/postprocess.go#L2325-L2387)）。

## 证书、代理、权限与安全边界

这是整个方案最重要的风险面。

- 安装根 CA 会改变系统全局信任链，使本机代理能解密和修改 HTTPS。仓库文档也明确警告这一点（[`certificate.md#L7-L13`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/docs/guide/certificate.md#L7-L13)）。
- 默认 CA **连同私钥一起硬编码在仓库/二进制中**（[`certificate.go#L7-L23`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/certificate/certificate.go#L7-L23)）。项目自身将它标为“硬编码密钥对，存在安全风险”，建议替换为本机专有证书（[`certificate service#L163-L186`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/internal/services/certificate.go#L163-L186)）。不建议在日常主机长期信任默认 SunnyNet CA。
- 更安全的做法是为每台机器生成独立 CA/私钥，并通过 `cert.file`、`cert.key`、`cert.name` 配置；两文件必须配对且均可读（[`cert.md#L7-L31`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/docs/config/cert.md#L7-L31)）。不使用时卸载 CA，而不只是退出程序。
- 默认 API 和代理只绑定 loopback。不要把 API host 改成 `0.0.0.0` 或暴露到局域网：视频号 WebSocket 的 `CheckOrigin` 无条件返回 true，且这里没有看到通用 API 鉴权（[`client.go#L21-L27`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/client.go#L21-L27)）。
- 系统代理会影响整机流量。TUN 模式会创建虚拟网卡并改路由，官方文档明确提示会与 Clash/Surge 等 VPN 冲突；Windows 默认无需 TUN（[`proxy.md#L5-L29`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/docs/config/proxy.md#L5-L29)）。
- Cookie、浏览记录、任务元数据和 URL 会进入本机工作目录/SQLite；默认工作目录是可执行文件所在目录，日志写入系统临时目录（[`workdir.md#L7-L31`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/docs/config/workdir.md#L7-L31)，[`main.go#L51-L66`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/main.go#L51-L66)）。这些内容不应上传到诊断报告或云端。
- 分享链接直解析会把分享 URL 和用户的元宝 Cookie发送给腾讯元宝官方域名，再请求腾讯视频号官方域名；当前源码未显示这一链路把 Cookie发送给第三方，但 Cookie 本身仍是敏感登录凭据。

只应下载本人创作、已获授权或平台允许保存的内容；本报告不评价具体内容的版权许可，也不建议绕过私密、删除、地区或账号访问控制。

## 常见失败与处理

| 现象 | 主要原因 | 处理 |
| --- | --- | --- |
| 微信里没有下载按钮 | 系统代理未指向 `127.0.0.1:2023`、CA 不受信任、微信未重启、页面改版导致注入正则失效 | 检查 Windows 代理；确认 CA；重启下载器和微信；尝试详情页/悬浮兜底按钮 |
| 分享链接解析提示缺少 Cookie 或 client socket | 无元宝 Cookie，同时没有已连接的视频号页面 | 先启动代理并在微信打开任意视频号页面，确认 `/api/channels/status` 的 `available=true`；或配置有效元宝 Cookie |
| 视频信息为空/下错当前视频 | 视频尚未播放，feed 切换状态未同步 | 等视频实际播放后暂停再点下载；更新到 `v260830`，该版继续修复首页详情识别（[`260830.md#L9-L15`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/docs/releases/260830.md#L9-L15)） |
| 启动报端口占用 | `2022` API 或 `2023` 代理已被占用 | 结束旧进程，或成对修改配置和调用端地址 |
| 开启后网络异常 | 程序异常退出留下系统代理，或 TUN/VPN 路由冲突 | 关闭系统代理/恢复原代理；禁用 TUN；正常退出下载器 |
| 下载后 MP4 无法播放 | `decodeKey` 未抓到、过期 URL、解密未执行或上游字段变化 | 重新打开并播放目标视频后重新建任务；检查任务日志中是否有 `decode_key` 和后处理错误 |
| MP3/直播后处理失败 | PATH 中缺少 `ffmpeg`/`ffprobe` | 安装同版本 ffmpeg/ffprobe并加入 PATH；普通 MP4 路径可绕开转换 |
| 杀毒软件告警 | 常规 Windows 包经 UPX 压缩 | 使用官方 Release 的 `safe` 包；仍需自行校验 checksums，不应因“safe”字样跳过信任判断 |
| 长标题下载失败 | 旧版 Windows 文件名/后缀处理问题 | 使用 `v260823` 或更高；该版本发布说明明确修复（[`260823.md#L9-L21`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/docs/releases/260823.md#L9-L21)） |

## 维护状态、版本和许可风险

- 截至研究日，仓库约 8.9k stars、1.4k forks，未归档，最新提交与 `v260830` 均为 `2bcc78bd...`；2026 年 8 月仍频繁适配微信页面、直播、分享链接和下载任务，属于活跃维护。
- 活跃不等于稳定。核心注入依赖对微信压缩 JS 的函数名和代码形状做正则替换（[`interceptor.go#L31-L68`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/interceptor.go#L31-L68)），微信改版即可失效；近期发布说明连续出现首页详情、内嵌聊天窗口、按钮和下载目标错位修复。
- Release 工作流由 tag 触发并产出 checksums，但 GitHub Actions 使用 `latest` GoReleaser、构建时还执行 `go run ...@latest` 生成 Windows 资源，降低可复现性；macOS 签名/公证目前被 TODO 禁用（[`release.yml#L1-L17`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/.github/workflows/release.yml#L1-L17)，[`release.yml#L65-L76`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/.github/workflows/release.yml#L65-L76)，[`goreleaser.yaml#L43-L48`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/build/goreleaser.yaml#L43-L48)）。main 分支无独立通用测试门禁，接入方必须固定版本并自建验收测试。
- 顶层 LICENSE 是 **MIT + Commons Clause**，明确不授予“Sell the Software”的权利，而不是标准 MIT（[`LICENSE#L1-L17`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/LICENSE#L1-L17)）。若 BiliSum 商业分发、把其核心能力作为付费产品价值来源，不能假定可直接复制或捆绑，需取得作者单独许可。本报告不构成法律意见。

## 能否接入 BiliSum

**技术上可接，建议 sidecar 集成；不建议把上游源码或发布二进制直接嵌进 BiliSum。**

BiliSum 当前 `normalize_video_url()` 只识别 Bilibili、YouTube 和抖音；探测层和真实 pipeline 也显式只允许这三类 URL。现有抖音实现是无状态 Python downloader，但视频号方案需要长期运行的代理、根 CA、微信页面 WebSocket、Cookie 状态和下载后解密，生命周期完全不同。

推荐边界：

1. 把视频号捕获器作为**用户显式安装和启动的本机 sidecar**，BiliSum 不自动提权、不静默安装 CA、不替用户修改系统代理。
2. BiliSum 增加 `wxchannels` URL 识别和 `WxChannelsProvider`，只连接 `127.0.0.1:2022`；先查询健康状态和“视频号页面/分享链接”可用状态，再提交分享 URL。
3. 由 sidecar 完成详情获取、CDN 下载和解密，输出到 BiliSum 为该任务分配的短路径，例如 `source.wxchannels.mp4`。BiliSum 只在文件完成且 `ffprobe` 验证通过后进入现有音轨提取、ASR 和视觉分析流程。
4. 不让 BiliSum 接收或记录 `decode_key`、元宝 Cookie、微信 Cookie、CA 私钥；日志只保留任务 ID、平台错误码和脱敏状态。
5. 增加明确的设置页：sidecar 地址（强制 loopback）、连接状态、证书风险说明、打开上游管理页、清除/卸载说明。任何证书或系统代理变更都保持为用户可见操作。
6. 固定兼容版本 `v260830`，为分享 URL、无页面连接、Cookie 失效、URL 过期、缺 key、解密失败、sidecar退出和 Windows 长路径建立回归测试。

也可以直接参考上游 Go 解密算法重写一个最小 Python adapter，但这仍解决不了最难的“如何从登录微信上下文稳定取得详情、URL 和 decodeKey”，并带来 Commons Clause、持续跟随微信私有页面变化及根证书管理成本。因此 sidecar 是当前更清晰、可撤销、许可边界更可控的方案。

## 对给定链接的实际判断

`https://weixin.qq.com/sph/AcPb6XFo5S` 符合上游严格正则 `^https://weixin\\.qq\\.com/sph/[A-Za-z0-9_-]+/?$`（[`client.go#L29-L31`](https://github.com/ltaoo/wx_channels_download/blob/2bcc78bd1641985405f362226a9dc9b50eb2b3cd/pkg/scraper/wxchannels/client.go#L29-L31)），因此会进入分享链接解析流程。

最可靠的下载方式仍是：启动下载器并确认代理/证书正常，在微信 PC 里打开该链接，让视频开始播放后暂停，再点注入的下载按钮。直接在 BiliSum 当前版本粘贴该链接仍不会成功，因为 BiliSum 尚未实现 `wxchannels` provider；本报告没有修改业务代码，也没有运行或安装上游发布二进制。

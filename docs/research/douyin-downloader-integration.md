# `douyin-downloader` 接入 BiliSum 的可行性研究

研究日期：2026-09-01  
目标仓库：[`Lyx10000/douyin-downloader`](https://github.com/Lyx10000/douyin-downloader)  
审计提交：[`04f698a6b2c3f4fdee29ae5f485192b897947db2`](https://github.com/Lyx10000/douyin-downloader/tree/04f698a6b2c3f4fdee29ae5f485192b897947db2)

## 结论

**可以接入抖音视频下载，但不建议把该仓库直接作为 BiliSum 的运行时依赖，也不建议直接启动它的 CLI。**

较稳妥的方式是：固定到已审计提交，参考或引入其中最小必要的 Python 解析逻辑，在 BiliSum 内部重构成非交互的 Douyin adapter；由 BiliSum 管理 Cookie、浏览器路径、下载目录、超时、TLS、日志和 ffmpeg 合并。先只支持单个公开视频作品，返回一个可供现有 ASR/图文流程消费的 MP4；图集和 BGM 后续单独建模。

直接依赖的主要阻碍不是功能缺失，而是工程边界：仓库没有 Python 包元数据或版本化 library API，主入口是无限交互循环，默认运行方式偏 Linux/Termux，浏览器发现逻辑不能直接覆盖常见 Windows Chrome 安装，而且下载请求关闭了 TLS 证书校验。仓库当前也没有 tag、release 或 CI，不能把 `main` 当成稳定上游接口。

## 能力与机制

### 语言和运行方式

- 仓库同时包含 Kotlin Android、Python、旧 Electron 实现；GitHub 的[语言统计](https://api.github.com/repos/Lyx10000/douyin-downloader/languages)显示主体为 Kotlin 和 Python。BiliSum 应只评估 Python 实现，旧 Electron 代码使用 Electron 8 和 `remote` API，不适合作为当前桌面应用的集成基础（[package.json](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/electron/package.json)、[index.js](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/electron/index.js)）。
- Python 依赖只有 `requests>=2.31,<3`、`selenium>=4.20,<5`、`gmssl>=3.2,<4`（[requirements.txt](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/requirements.txt)）；媒体处理还依赖系统 `ffmpeg`。README 的安装示例要求 Debian/Termux 下的 `chromium`、`chromium-driver` 和 `ffmpeg`（[README](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/README.md)）。
- `douyin_downloader.py` 可以被 import，且 `parse_douyin`、`download_video` 等函数可单独调用，但仓库没有 `pyproject.toml`/`setup.py`、公开 API 契约或语义版本。其裸模块导入还要求把 `python3/` 加入 `sys.path`（[douyin_downloader.py](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py)）。因此“可以调用”不等于“适合作为库依赖”。
- Android/Chaquopy 路径已有非交互 `parse_share(share_text, cookie_header)`，会返回规范化 JSON 和分类错误码，可参考它设计 BiliSum 的 probe 返回模型（[android_bridge.py](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/android_bridge.py#L207)）。但它依然是应用内部桥接函数，不是版本化 SDK，而且依赖调用方先提供有效 Cookie。
- CLI 是持续读取 `input()` 的交互循环，质量和下载模式也通过 `input()` 选择；末尾还无条件复制到 `/mnt/Android/douyin_download/`。这与 BiliSum 的后台任务模型、Windows 目录和取消/进度语义不兼容，不应以 subprocess 包装该 CLI（[主入口源码](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L925)）。

### 短链、Cookie 与签名

解析链路如下：

1. 用 `requests` 跟随分享短链跳转，从 `/video/{id}`、`/note/{id}` 或长数字路径提取作品 ID。
2. 启动持久 profile 的无头 Chromium，访问作品页以产生 `__ac_signature`、`ttwid`、`odin_tt` 等安全 Cookie。
3. 将 Cookie 与本地生成的 `a_bogus` 签名用于作品详情请求，取得视频清晰度、DASH 音轨或图集元数据。
4. 接口失败时，回退解析页面的 React `self.__pace_f` 数据，再回退到播放器元素和 Performance API 中的媒体 URL。

实现见 [`parse_douyin` 与浏览器回退](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L57)和 [`fetch_douyin_detail`](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_quality.py#L63)。`a_bogus` 实现不是目标仓库原创，而是派生自 `Johnserf-Seed/f2`；仓库对此提供了 Apache-2.0 声明（[THIRD_PARTY_NOTICES](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/THIRD_PARTY_NOTICES.md)）。

公开内容不一定需要账号登录，但仍依赖浏览器产生的风控 Cookie。用户也可通过 `DOUYIN_COOKIE` 补充登录态；浏览器数据持久化在源码目录下的 `.douyin-browser-profile/`（[README](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/README.md)、[douyin_quality.py](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_quality.py#L63)）。BiliSum 不能照搬这个存储位置：应放入应用私有数据目录，避免日志输出 Cookie，支持用户清除登录态，并限制权限和生命周期。

### 视频、无水印、图集和 BGM

- 视频：详情数据中会提取多个分辨率、帧率、码率和 H.264/H.265 档位，默认选择排序后的最高档；DASH 视频与音频由 ffmpeg `-c copy` 合并，不重编码（[douyin_quality.py](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_quality.py#L172)、[download_video](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L731)）。
- 无水印：旧页面回退明确把 `/playwm/` 替换为 `/play/`；新版主要使用详情接口提供的播放地址（[旧回退实现](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L448)）。这是一种基于当前平台 URL 语义的实现，不是抖音提供的稳定无水印契约，字段或风控变化后可能失效。
- 图集：优先 `download_url*`，再用 `origin_url`，最后用质量较高的 `url_list` CDN 地址；原样保存，不转换或二次压缩（[extract_image_urls](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_quality.py#L260)）。README 明确说明“最高档”只代表当前接口公开的最高质量，不保证作者上传前母文件。
- BGM：图文作品可返回直接音乐地址；旧页面路径也可下载幻灯片视频后用 ffmpeg 提取音轨（[资源映射](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L391)、[download_bgm](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L871)）。

## 许可证与合规

- 顶层 [`LICENSE`](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/LICENSE) 是 MIT，Copyright (c) 2020 kajweb。与同为 MIT 的 BiliSum 兼容，但分发包含或改写的代码时必须保留版权和许可文本。
- `python3/abogus.py` 受 Apache-2.0 约束；若引入该文件，需保留其原有版权/许可头，并把 Apache-2.0 文本和第三方声明带入 BiliSum 的分发 notice（[第三方声明](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/THIRD_PARTY_NOTICES.md)）。
- README 同时写有“仅供学习交流使用，请勿商用”，而 MIT 正文允许使用、复制、修改、分发和销售。两处表述存在明显张力。若 BiliSum 将来商业分发，建议在复制实现前向仓库所有者确认意图，并记录确认结果；本报告不构成法律意见。
- 下载器依赖未公开、会变化的 Web 详情接口、反自动化 Cookie 和签名算法。仓库自己的 Android 文档也明确写明“抖音没有公开稳定的下载 API，Cookie、签名或字段变化后可能需要更新”（[Android README](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/android/README.md#L52)）。这意味着维护成本和平台政策风险都应由 BiliSum 接口层隔离，UI 应提醒用户只处理其有权访问和保存的内容。

## Windows 与安全风险

| 风险 | 证据 | BiliSum 处理建议 |
| --- | --- | --- |
| 浏览器发现不兼容常见 Windows 安装 | Python 代码只执行 `shutil.which("chromium")` / `chromium-browser` 和 `chromedriver`，且显式创建 Selenium `Service`（[源码](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L190)） | 支持配置 Chrome/Chromium 与 driver 路径；优先复用应用已管理的浏览器或 Selenium Manager，并验证版本匹配 |
| TLS 验证被关闭 | 普通 Session 和媒体下载都设置 `verify=False`，同时禁用 urllib3 警告（[Session](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L48)、[下载请求](https://github.com/Lyx10000/douyin-downloader/blob/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/douyin_downloader.py#L684)） | 集成时恢复默认 TLS 验证；只有明确可诊断的企业代理配置才能使用自定义 CA，不能静默跳过验证 |
| Cookie 为敏感凭据 | profile 持久化，且支持环境变量明文 Cookie | 存应用私有目录，不进入任务导出/诊断日志；提供删除入口；媒体 CDN 请求只带必要 Referer/UA，不传播站点 Cookie |
| 下载 URL 和字段不稳定 | 浏览器、签名 API、React 页面数据和播放器 URL 多级回退 | 独立 adapter、结构化错误码、短 TTL 缓存、故障回退；固定上游提交并用真实样本定期验收 |
| 文件和资源滥用 | 原实现没有下载体积上限，媒体读取超时可到 120 秒 | 预检 Content-Length、设置单任务大小/时长/并发限制，支持取消并清理临时文件 |
| 编解码兼容性 | 最高档可能为 H.265；DASH 需要 ffmpeg | 为 ASR/截图流程优先选择可解码档位，ffprobe 验证产物；合并失败时给出明确错误而不是把无声视频交给下游 |

## 维护成熟度

正面信号：仓库在审计日仍有连续提交，最新提交为 2026-09-01；Python 目录包含质量选择、图片 URL 和 ffmpeg 合并测试。本地使用 BiliSum 的 Python 3.12 环境执行仓库全部测试，结果为 **22 passed**，其中包括实际 ffmpeg stream-copy 测试（[tests](https://github.com/Lyx10000/douyin-downloader/tree/04f698a6b2c3f4fdee29ae5f485192b897947db2/python3/tests)、[提交历史](https://github.com/Lyx10000/douyin-downloader/commits/main/)）。

谨慎信号：GitHub [仓库元数据](https://api.github.com/repos/Lyx10000/douyin-downloader)显示目标仓库创建于 2026-09-01；审计时为 0 stars、0 forks、0 open issues。仓库共有 26 个提交，其中前期历史来自 2019-2020，近期集中修改发生在 2026-08-20 至 2026-09-01。审计时 [tags](https://api.github.com/repos/Lyx10000/douyin-downloader/tags) 和 [releases](https://api.github.com/repos/Lyx10000/douyin-downloader/releases) 均为空，且 `.github` 下没有 CI workflow。现有测试覆盖数据归一化和媒体合并，但没有实时抖音端到端测试。因此它证明了“近期有人在改”，尚不能证明上游 API 稳定或发布流程成熟。

## 推荐接入边界

建议新增一个 BiliSum 内部的、平台无关下载接口，并让抖音实现隐藏所有反爬细节：

```python
class SourceDownloader(Protocol):
    def supports(self, url: str) -> bool: ...
    def probe(self, url: str) -> SourceMetadata: ...
    def download(self, url: str, target: Path) -> DownloadedMedia: ...
```

Douyin adapter 第一阶段只需要交付：规范化后的作品 URL/ID、标题和封面（若可得）、单个最终 MP4、原始平台标识、选择的清晰度，以及可分类展示的异常。不要把上游的 `dict` 结构扩散到 pipeline、repository 或 UI。

实施顺序建议：

1. 为抖音分享文本、短链、`/video/` 和 `/note/` URL 增加纯函数识别与规范化测试。
2. 从上游提取最小的 `a_bogus`、详情解析和媒体档位逻辑，保留许可头；删除小红书、Android 同步和交互选择代码。
3. 把浏览器/Cookie 获取做成可替换 provider，Windows 下从应用配置解析可执行文件路径。
4. 下载到任务临时目录，启用 TLS 校验、大小限制、取消和清理；使用现有 ffmpeg 运行时合并为最终 MP4。
5. 以录制的脱敏详情 fixture 覆盖视频、DASH、图集、过期签名、403 和字段变更；再做一个手动启用的真实抖音 smoke test。
6. UI 第一版仅承诺公开视频和单视频作品；遇到登录/验证、私密、删除、地区限制或图集时给出明确状态，不自动绕过访问控制。

## 最终决策

**Go（有条件）**：可以开发抖音下载接入，预计可复用该仓库的核心思路和一部分 Python 代码。

**No-Go（直接依赖）**：不要 `pip install`、git submodule、跟随 `main`，也不要把交互 CLI 当作子进程塞进 BiliSum。采用内部 adapter、固定审计版本、保留许可证、重做 Windows 浏览器发现与 TLS/Cookie 边界，才能达到 BiliSum 当前桌面服务的工程要求。

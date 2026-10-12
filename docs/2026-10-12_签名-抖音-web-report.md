# 抖音视频详情请求签名适配记录

分析日期：2026-10-12。修正后，已保存的 Cookies 可用于服务器实时解析两个实际作品；不需要保存浏览器中复制的一次性签名。

## 范围

目标为 BiliSum 的 `GET https://www.douyin.com/aweme/v1/web/aweme/detail/` 请求兼容性。分析使用用户提供的浏览器请求和登录态，以及官网公开加载的 Web SDK。真实 Cookie、设备标识及带登录参数的完整请求均未纳入仓库。

## Evidence：观察与复现

| 编号 | 来源 | 观察 | 复现方式 |
| --- | --- | --- | --- |
| E-001 | 服务器网络响应，2026-10-12 | 原请求返回 `403 Blocked by ArgusSecurityPlugin Uifid Not Found`；补充 UIFID 后返回 `Signature Not Found`。 | 旧实现配合登录 Cookies 请求该接口；具体登录态为私有输入，未发布。 |
| E-002 | 用户浏览器请求及官网 SDK，2026-10-12 | 完整浏览器请求在本机和服务器均返回 HTTP 200。SDK 的 `SDKRuntime.global.webSignUrl` 添加时间戳和新的查询签名。 | 登录自己的抖音账号后，在 Network 中观察详情请求；不能复用本文的合成测试设备标识进行实际登录。 |
| E-003 | SDK 合成测试向量及单元测试，2026-10-12 | Python 生成的签名与官网 SDK 完全一致，覆盖中文、空格、空值及 `+ / =` 的编码；每次请求生成新时间戳和签名。 | 在仓库根目录运行 `.venv\Scripts\python.exe -m pytest tests/unit/test_douyin.py -q`。 |
| E-004 | 部署后的服务请求，2026-10-12 | 正常服务的 `probe_video_asset` 成功解析作品 `7695291560069483814` 和短链接对应的 `7684876053558365459`，时长分别为 364.167 和 245.413 秒。 | 使用自己的有效 Cookies 导入 BiliSum 后，解析上述公开作品链接；平台可用性及登录状态会影响实时结果。 |

E-001、E-002、E-004 为实时网络观察，原始认证请求不公开，`content_hash` 为 n/a。E-003 是仓库内可独立执行的确定性验证。

## Finding：根因与修正

F-001：现有 `a_bogus` 签名不能替代新的 Web SDK 签名。结论状态为 validated，可信度 high，证据为 E-001、E-002、E-003、E-004。此前将所有 403 归因于登录态失效会误导用户重复导入 Cookies。

修正位置为 `packages/core/src/video_sum_core/douyin.py` 的 `_web_sign_query` 和 `DouyinDownloader._fetch_detail`：从导入的 Cookie 读取 `UIFID`，兼容 `UIFID_TEMP` / `UIFIDTEMP`，在请求中加入浏览器标识、当前时间戳及新的签名，同时保持原有 `a_bogus` 流程。

Web 签名的材料依次为：

1. UIFID。
2. 当前秒级 Unix 时间戳。
3. 官网 SDK 的协议常量 `A96D855A08C0A9707F8BEF0D9A527E4E`。
4. 保留参数原有顺序的查询串，追加时间戳；值按照 JavaScript `encodeURIComponent` 编码，空格编码为 `%20`，保留空值。

四段用 `_` 连接，取 UTF-8 字节的 MD5 十六进制结果。该 MD5 用于匹配接口协议，不用于保存密码。签名写入 `x-secsdk-web-signature` 查询参数；请求头同步携带 `uifid`、`x-secsdk-web-signature` 和 `x-secsdk-web-expire`。

固定测试向量使用合成标识 `test-device` 和时间戳 `1791795817`，输入含 `a_bogus=a%2Bb%2Fc%3D`、空值及中文空格，期望签名为 `caa33fc595c8c65bd6d9d12cf70a1bb2`。完整输入及断言见 `tests/unit/test_douyin.py::test_web_signature_matches_browser_sdk_vector`。

## Path：实际调用链

P-001，类型 callflow：从已保存的 Cookie 到正常的视频详情解析。

1. 读取合并后的登录 Cookie，提取 UIFID；对应 E-001 和 F-001。
2. 生成原有 `a_bogus`，再按 SDK 规则生成时间戳和 Web 签名；对应 E-002、E-003 和 F-001。
3. 请求详情接口，通过原有元数据解析流程返回作品信息；对应 E-004 和 F-001。

当前验证覆盖详情解析及签名一致性，没有执行完整 ASR/总结任务。此实现匹配分析当日的官网协议；未来协议变化应通过新的浏览器请求和测试向量重新确认。

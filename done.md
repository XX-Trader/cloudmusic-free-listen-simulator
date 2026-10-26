# Done

## 2026-10-26

- [x] 修复离线状态机的六项复核缺陷：阶段时间倒退、可恢复拒绝的重试路径、停留阶段不可跳过、执行器审计串场、场景时间配置校验、`AutoRunner` 函数长度。
- [x] 补齐测试证据层：真实 Python CLI 子进程、真实 PowerShell 包装器一致性、进程内审计钩子零网络守卫、审计字段与场景预期表断言。
- [x] 只读探明网易云接口可用集合：`user/playlist`、`v6/playlist/detail`、`song/detail`、`song/enhance/player/url` 可用；`playlist/track/all`、`cloudsearch`、`recommend/songs` 被服务端拒绝，未纳入实现。
- [x] 实现 P3 只读播放验证 `vip_probe.py`：权益门禁 → 自动筛选 VIP 候选 → 播放授权判定 → 32 KB 有界取流。
- [x] 实现 P4 显式桌面启动器 `start_desktop.ps1`：客户端解析、启动、等待、门禁与探针联动，输出 JSON 摘要与退出码。
- [x] 修正 `account_gate` 以支持注入只读请求器并对外隐藏账号原值。
- [x] 建立“VIP 播放验证”与“桌面启动器”两套功能三件套。
- [x] 手机端无线调试接入并定位官方任务入口：首页 → 免费听 → 领 30 分钟，任务条件为点击落地页并停留 8 秒。
- [x] 观测到官方免费听流程的完整接口链：`ad/get`、`ad/start/info/upload`、`ad/monitor/*`、`ad/listening/rights/gain`、`free/tab/asset`、`song/enhance/privilege`。
- [x] 定位奖励发放接口 `/eapi/ad/listening/rights/gain`，记录实测时序并写入官方流程接口地图。
- [x] 实现链路提取工具 `api_trace.py`，可从 logcat 输出结构化调用链并识别奖励接口。
- [x] 实现 P5 手机端自动化 `device_automation.py`：唤醒解锁、界面状态机、广告选择页下滑取卡、视频广告页坐标兜底、系统弹窗、8 秒停留、返回客户端、权益轮询与播放验证。
- [x] 修正播放判定规则：实测权益生效时 `freeTimeTrialPrivilege` 仍为 false，权威判据是 `cannotListenReason` 为空且返回完整时长地址。
- [x] 真机取得官方 30 分钟权益并跑出绿灯：`entitled`、206 取流 32 KB 带 ID3 头、退出码 0。
- [x] 单元测试扩充到 74 项，全部通过。

## 2026-10-25

- [x] 建立免费听任务的纯离线状态机，覆盖点击、停留门槛、服务拒绝、任务过期和重复领取。
- [x] 建立真实账号权益只读门禁，从当前 Windows 用户的网易云桌面会话查询登录状态和剩余时长。
- [x] 凭证仅在内存中使用，输出仅包含账号哈希和剩余时长。
- [x] 完成 8 项离线模拟测试和 3 项账号门禁测试。
- [x] 公开 `codex/offline-simulator` 分支，基线提交为 `66ace5a9`。
- [x] 建立项目文档索引、路线图与“免费听离线模拟”功能三件套。
- [x] 实现 CLI 全自动执行器：签发→启动广告→模拟点击→虚拟停留→自动领取→结构化审计轨迹。
- [x] 新增一键 Mock 接口 `mock_api.py`：仅监听 127.0.0.1，`POST /v1/demo/run`、`GET /v1/demo/health`，请求体上限 4096 字节。
- [x] 新增本地界面模拟器 `web/index.html`：完整复刻首页→免费听→广告→点击→停留倒计时→领奖→余额到账。
- [x] 明确排除真机自动点击广告与真实领奖，写入 todo 的“明确不做”。
- [x] 通过官方手机任务取得一次真实权益：广告落地页停留 8 秒后到账 30 分钟，手机与服务端只读查询一致。

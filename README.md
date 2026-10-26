# 免费听任务流程模拟器

这是一个独立编写的离线演示程序，用于验证广告任务、自动点击、虚拟停留、自动领取、服务拒绝和重复领取的处理。

离线模拟器不连接网易云或广告服务，所有任务、点击、余额和回执都存放在当前进程内存中。运行离线场景不需要手机、账号、Cookie 或第三方依赖。

## CLI 全自动流程

每次运行会自动完成：

1. 本地演示服务签发任务。
2. 启动虚拟广告。
3. 在配置的虚拟时间记录模拟点击。
4. 进入虚拟停留阶段。
5. 自动向本地演示服务领取奖励。
6. 输出状态事件、结构化审计轨迹、演示回执和演示余额。

所有场景都使用虚拟时间并立即完成，不会等待现实中的广告时长。

## 运行

需要 Python 3.10+ 和 PowerShell 7：

```pwsh
pwsh -NoProfile -File ./run.ps1
pwsh -NoProfile -File ./run.ps1 -Scenario short-stay
pwsh -NoProfile -File ./run.ps1 -Scenario server-rejected
pwsh -NoProfile -File ./run.ps1 -Scenario duplicate
```

可通过 `-PythonPath` 指定 Python 可执行文件。

## 场景

| 场景 | 自动行为 | 预期结果 |
| --- | --- | --- |
| `success` | 启动、点击、停留 8 秒后领取 | 演示余额增加 30 分钟 |
| `short-stay` | 启动、点击、停留不足 8 秒后领取 | 回执拒绝，余额为 0 |
| `server-rejected` | 满足停留后由演示服务拒绝 | 回执拒绝，余额为 0 |
| `duplicate` | 成功领取后再次自动领取 | 返回首次回执，余额仍为 30 |

固定的 8 秒和 30 分钟仅为测试参数，不代表平台当前规则。

## 输出保证

任意场景的 JSON 都包含以下证明字段：

```json
{
  "mode": "offline_demo",
  "real_entitlement_granted": false,
  "network_requests": 0,
  "automation": {
    "scope": "local_demo_only",
    "simulated_ad_started": true,
    "simulated_click_executed": true,
    "simulated_claim_executed": true
  }
}
```

`audit_log` 按顺序记录虚拟时间、动作、结果和原因。成功领取的 `receipt.demo` 始终为 `true`；`demo_balance_minutes` 只表示当前进程中的演示余额。

未知任务、未点击、停留不足、任务过期、时间倒退及服务拒绝均不会增加演示余额。重复领取返回同一回执，不重复增加余额。

## 一键接口

除 CLI 外，还可以用 HTTP 接口驱动同一套离线流程。服务只绑定 `127.0.0.1`，不会监听局域网：

```pwsh
pwsh -NoProfile -Command "python -B ./mock_api.py --port 8848"
```

```bash
curl -X POST http://127.0.0.1:8848/v1/demo/run \
  -H "Content-Type: application/json" \
  -d '{"scenario":"success"}'
```

| 方法与路径 | 作用 |
| --- | --- |
| `GET /v1/demo/health` | 返回离线状态、可用场景和实际绑定地址 |
| `POST /v1/demo/run` | 一键执行完整流程，返回审计轨迹与演示回执 |

接口约束：

- 请求体上限 4096 字节，必须是 UTF-8 JSON 对象。
- 场景名必须来自注册表，否则返回 400。
- 写入口不接受 GET（405），未知路径返回 404。
- 服务不读取任何请求头中的凭证，不记录访问日志。

## 界面模拟器

`web/index.html` 用浏览器直接打开即可，无需安装依赖。它把手机端的完整交互复刻为本地页面：

首页 → 免费听 → 领 30 分钟 → 广告页 → 点击广告 → 15 秒停留倒计时 → 领取奖励 → 余额到账。

页面内所有数据都在内存中：没有网络请求，没有账号凭证，广告画面和品牌均为本地绘制，刷新后余额归零。停留秒数与奖励分钟数在文件顶部的 `STAY_SECONDS`、`REWARD_MINUTES` 两个常量里，可自行调整。

## 测试

```pwsh
pwsh -NoProfile -Command "python -B -m unittest discover -s tests -v"
```

测试覆盖底层时间和状态边界、四个 CLI 场景、自动审计轨迹、重复领取幂等性、Python 与 PowerShell 的场景集合一致性、真实 CLI 与 PowerShell 入口的输出一致性、离线核心的零网络守卫、VIP 播放探针的判定边界，以及桌面启动器的参数与摘要结构。

## 真实账号到账门禁

Windows 版网易云音乐已登录时，可以只读检查免费听剩余时长：

```pwsh
pwsh -NoProfile -Command "python -m venv .venv; ./.venv/Scripts/python.exe -m pip install -r requirements-account-gate.txt"
```

然后运行：

```pwsh
pwsh -NoProfile -Command "python -B ./account_gate.py"
```

完成官方手机任务后需要等待到账时，可设置有界轮询窗口：

```pwsh
pwsh -NoProfile -Command "python -B ./account_gate.py --wait-seconds 60 --poll-seconds 5"
```

`account_gate.py` 仅在 `rightsRemainingTime > 0` 时开放后续 VIP 播放测试门禁。它读取当前 Windows 用户的网易云桌面会话，凭证只在内存中使用，输出仅包含账号哈希和剩余时长。

## VIP 播放验证

权益到账后，可以只读验证这份权益是否真的能放行 VIP 歌曲：

```pwsh
pwsh -NoProfile -Command "python -B ./vip_probe.py"
pwsh -NoProfile -Command "python -B ./vip_probe.py --song-id 65800"
pwsh -NoProfile -Command "python -B ./vip_probe.py --skip-stream"
```

探针会读取账号歌单，自动挑出 `fee == 1` 的 VIP 曲目，读取其播放授权并给出结论：

| 结论 | 含义 | 退出码 |
| --- | --- | --- |
| `entitled` | 免费听权益放行，且播放地址可达 | 0 |
| `gate_closed` | 权益为 0，未发起任何候选与播放请求 | 2 |
| `blocked` | 平台返回阻断原因 | 3 |
| `unknown` | 授权字段不足以判定 | 4 |

取流只请求播放地址的前 32 KB，不下载完整音频。探针不提交广告完成、点击、停留或领奖请求。

## 手机端自动化

`device_automation.py` 通过 ADB 驱动官方客户端走完免费听任务，并把到账与播放验证串成一条命令：

```pwsh
pwsh -NoProfile -Command "python -B ./device_automation.py"
pwsh -NoProfile -Command "python -B ./device_automation.py --serial <设备序列号> --wait-seconds 120"
```

流程为：唤醒解锁 → 启动网易云 → 进入免费听 → 打开广告任务 → 点击落地页并停留 8 秒 → 返回客户端 → 轮询权益 → 验证 VIP 播放。

- 元素按文案定位，标题与按钮同名时按宽度过滤；视频广告页无法导出界面时用登记坐标兜底。
- 退出码：`0` 全部通过、`2` 权益未到账、`3` 播放未放行、`5` 无设备、`6` 手机端流程失败。
- 输出含 `baseline_rights` 与 `rights_delta`，可看出权益变化。
- 只操作官方客户端的可见界面，不调用广告或领奖接口。

## 官方接口链路

`api_trace.py` 解析客户端自己的 logcat，输出结构化调用链并识别奖励发放接口：

```pwsh
pwsh -NoProfile -Command "python -B ./api_trace.py --log <logcat文件>"
```

实测的奖励发放接口是 `/eapi/ad/listening/rights/gain`，调用时序见 [官方流程接口地图](docs/网易云/官方流程接口地图.md)。工具只读取已经发出的请求。

## 桌面启动器

`start_desktop.ps1` 把“启动网易云客户端 → 等待就绪 → 权益门禁 → 播放验证”串成一次显式执行：

```pwsh
pwsh -NoProfile -File ./start_desktop.ps1
pwsh -NoProfile -File ./start_desktop.ps1 -StartClient
pwsh -NoProfile -File ./start_desktop.ps1 -StartClient -Probe
```

- 默认不启动客户端；只有显式给出 `-StartClient` 才会拉起进程。
- 客户端路径按“显式参数 → 运行中进程 → 候选安装路径”解析，找不到时报错退出。
- 输出是一份 JSON 摘要，退出码含义见 [桌面启动器文档](docs/网易云/桌面启动器/README.md)。
- 不注册开机自启、计划任务或系统服务，不在后台常驻。

## 隔离边界

- 离线模拟器不调用真实任务、广告监控、点击、观看完成或领奖接口。
- 真实账号门禁不提交任何广告动作或权益变更请求。
- 播放验证只读播放授权与前 32 KB 音频，不下载完整音频。
- 手机端自动化只操作官方客户端界面，不构造、不重放广告或领奖请求。
- 项目不包含设备身份模拟、广告完成证明、客户端修改或进程注入。
- 不在真机上自动点击广告、自动停留或自动领取真实权益；这类行为属于虚假广告互动，不纳入实现。
- 项目与网易云没有隶属或授权关系。

详细需求、架构和实施状态见 [项目文档索引](docs/INDEX.md)。

# VIP 播放验证

## 1. 目标

在真实账号已通过官方任务取得免费听权益的前提下，只读验证这份权益是否真的能放行 VIP 歌曲播放。

验证链路：

```text
权益门禁 → 自动筛选 VIP 候选 → 读取播放授权 → 判定放行 → 有界取流确认可达
```

全过程只发起查询类请求，不提交广告完成、点击、停留或权益领取请求。

## 2. 子功能与状态

| 子功能 | 状态 | 验收依据 |
| --- | --- | --- |
| 权益门禁 | 已完成 | 余额为 0 时直接短路，不发起候选与播放请求 |
| 自动筛选 VIP 候选 | 已完成 | 歌单曲目 → 歌曲详情 `fee == 1` |
| 播放授权判定 | 已完成 | 依据 `freeTimeTrialPrivilege` 与 `cannotListenReason` |
| 有界取流确认 | 已完成 | 单次 Range 请求，读取不超过 32 KB |
| 指定歌曲验证 | 已完成 | `--song-id` 跳过歌单查询 |
| 真实权益下的绿灯 | 待补 | 需要一次新的官方任务权益 |

## 3. 范围边界

### 范围内

- 读取账号登录状态与免费听剩余时长。
- 读取账号歌单、歌曲详情与播放授权条目。
- 对播放地址发起一次有界 Range 请求确认数据可达。
- 输出不含凭证的验证结论。

### 范围外

- 提交广告完成、点击、停留与领奖请求。
- 提取、导出或持久化 Cookie、Token、设备标识。
- 下载完整音频；只读前 32 KB 判定可达性。
- 修改客户端文件、注入进程或改写本地数据。

## 4. 业务规则

1. 门禁未放行时立即返回 `gate_closed`，不产生候选与播放请求。
2. 候选只取自当前账号自己的歌单；默认优先“我喜欢的音乐”。
3. `fee == 1` 视为 VIP 曲目；候选歌曲数上限 20。
4. `cannotListenReason` 非零视为 `blocked`。
5. `userConsumable` 为真且 `remainTime > 0` 且无阻断原因，才判定 `entitled`。
6. 取流只允许一次 Range 请求，读取上限 32 KB，响应读取上限 64 KB。
7. 任何失败都抛出原始异常，不做静默回退。

## 5. 验收标准

- 权益为 0：`verdict = gate_closed`，请求数 2，退出码 2。
- 权益有效且候选可放行：`verdict = entitled`，`candidate.fee = 1`，退出码 0。
- 权益无效但接口仍返回地址：`verdict = blocked`，不发起取流。
- 输出 JSON 不含 Cookie、账号原值与手机号。
- 单元测试全部走假服务，不触网。

## 6. 风险与限制

- 平台接口路径可能调整；接口不可用时脚本以原始异常失败，不做猜测式降级。
- 只读判定依赖平台返回的授权字段，字段缺失时判定为 `unknown` 并以退出码 4 结束。
- 真实绿灯结论只在权益有效时成立，过期后复跑必然回到 `gate_closed`。

## 7. 工具与运行

- Python 3.10+
- `cryptography`（读取桌面会话 Cookie，见 `requirements-account-gate.txt`）

```pwsh
pwsh -NoProfile -Command "python -B ./vip_probe.py"
pwsh -NoProfile -Command "python -B ./vip_probe.py --song-id 65800"
pwsh -NoProfile -Command "python -B ./vip_probe.py --skip-stream"
```

## 8. 后续方向

与桌面启动器联动：启动器在客户端就绪后先跑门禁，再按需跑本探针。

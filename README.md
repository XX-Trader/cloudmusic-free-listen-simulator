# 免费听任务流程模拟器

这是独立编写的离线演示程序，用来验证广告任务、停留条件、领取回执和重复领取的处理。

**不会连接网易云、提交广告统计或领取真实权益。所有余额和回执都是本地演示数据。** 不需要手机、账号、Cookie 或第三方依赖。

## 运行

需要 Python 3.10+ 和 PowerShell 7：

```pwsh
pwsh -NoProfile -File ./run.ps1
pwsh -NoProfile -File ./run.ps1 -Scenario short-stay
pwsh -NoProfile -File ./run.ps1 -Scenario server-rejected
pwsh -NoProfile -File ./run.ps1 -Scenario duplicate
```

可通过 `-PythonPath` 指定 Python 可执行文件。

程序用虚拟时间验证流程，立即输出 JSON。成功场景的 `demo_balance_minutes` 为 30；所有场景的 `real_entitlement_granted` 都为 `false`。

## 模型

1. 演示服务签发任务。
2. 记录本地点击事件。
3. 按任务类型校验停留条件及有效期。
4. 演示服务返回同任务回执；客户端收到通过回执后才进入演示完成状态。
5. 重复领取返回同一回执，不重复增加演示余额。

固定的 8 秒、30 分钟是测试参数，不代表平台当前规则。未知任务、未点击、停留不足、任务过期、时间倒退及服务拒绝均不会增加演示余额。

## 测试

```pwsh
pwsh -NoProfile -Command "python -B -m unittest discover -s tests -v"
```

本项目没有实现真实权益接口、设备身份模拟或广告完成证明，也没有修改、捆绑或复制网易云客户端代码。与网易云没有隶属或授权关系。

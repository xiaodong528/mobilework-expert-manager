# 管理器版本、诊断与证据合同

## 目录

1. [版本输入](#版本输入)
2. [配置边界](#配置边界)
3. [结构化 findings](#结构化-findings)
4. [Gate 与证据等级](#gate-与证据等级)
5. [可信 sidecar](#可信-sidecar)
6. [退出码](#退出码)

## 版本输入

目标 OpenCode 版本按 `--target-opencode-version`、
`MOBILEWORK_TARGET_OPENCODE_VERSION`、`--host-contract`、`unknown` 的顺序解析。
版本字符串只证明来源和冲突，不自动证明能力。host contract 的 `capabilities` 仅在其版本与
生效目标版本一致时可作为显式能力证据。机器可读事实源是
`scripts/manager-contract.json`，不得在 SKILL、reference、测试或 eval 中维护固定版本副本。

当前管理器合同为 `2.1.0`。旧专家包仍可诊断、校验和安装；缺少角色 Reference 绑定时给兼容
warning。结构性修改前先生成迁移预览，让用户确认每项 Reference 的使用角色，以及既有 workspace
Instruction 是否仍应全局生效。不得静默把全局规则改成角色规则。

Reference 能力只看已核实的 host contract：

- `references=true` 时使用原生 local/Git 投影；
- 能力不支持或未知时，local Reference 在安装阶段降级为角色专属派生 Skill；
- 能力不支持或未知时，Git Reference 在写 workspace 前返回 `capability-missing`。

不能根据 OpenCode 版本号猜测 Reference 能力。Git 异步 materialize 成功属于独立 Runtime 证据。

## 配置边界

专家包配置始终位于 `<package>/opencode.json`；包内 `.opencode/**` 只保存文件型运行资源。
安装器读取根配置并按所有权合并到 `<workspace>/.opencode/opencode.jsonc`，receipt 位于
`<workspace>/.opencode/.expert-installs/<slug>.json`。旧 `.mobilework-engine` 只允许出现在
迁移检测、禁止路径或历史说明中。

## 结构化 findings

schema v2 的 finding 原生提供 `code`、`severity`、`phase`、`path`、`location`、
`rootCause`、`remediation` 和脱敏 `evidence`。尚未迁移的旧校验点才允许 catalog 分类；
已知 fixture 不得退化为无意义的通用错误码。`--schema-version 1` 只适配同一结果，不重新运行。

## Gate 与证据等级

Gate 固定为 `archive`、`contract`、`portability`、`install`、`configLoad`，取值只有
`passed`、`failed`、`blocked`、`not-run`。`evidenceLevel` 只有 `invalid`、`valid`、
`installable`、`config-loadable`。Runtime 状态独立为 `not-tested`、`blocked`、`verified`。
静态校验、安装或 pure config 不能描述成真实 Runtime 会话已加载。

provenance 至少记录 skill 路径与过滤后 hash、合同和 finding catalog 版本、Python、目标
OpenCode 来源、输入 hash、安全阈值及 UTC 时间。安装证据再记录临时目标和 receipt 摘要；
pure config 再记录显式 sidecar 路径与实际版本。不得记录 secret 或完整凭据。

## 可信 sidecar

只对当前管理器生成、验证并安装到临时 workspace 的可信包使用
`verify_trusted_config.py --sidecar <explicit-path>`。该脚本执行 `--version` 和
`debug config --pure`，不启动服务、不运行会话。不得自动搜索 sidecar，也不得用系统未知版本
冒充仓库锁定版本。目标版本冲突时停止。

## 退出码

- `0`：请求的 gate 通过；
- `1`：合同或安全 finding；
- `2`：参数、环境或版本合同错误；
- `3`：管理器内部异常；
- `4`：请求了策略禁止的运行操作。

warning 不使进程失败。

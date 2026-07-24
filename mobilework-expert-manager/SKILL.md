---
name: mobilework-expert-manager
description: >-
  创建、转换、修改、诊断、校验、安装、打包或版本发布 MobileWork 专家与专家团时使用；
  涵盖 expert.json、角色/Workflow、Skills、权限、MCP、运行时扩展、旧包迁移、Bundle、
  本地 Git/SemVer 和 workspace 投影。外部 ZIP、附件或未知目录默认只做无执行静态诊断。
---

# MobileWork 专家包管理器

以 `expert.json` 为结构与资源所有权唯一事实源。根 `opencode.json`、README、Agent、Skill 和
`.opencode/**` 文件型资源都是可重建派生物。默认用中文沟通；业务标识、路径和代码保持原文。

## 安全边界

- 外部 ZIP、附件或未知目录只运行 `diagnose_expert.py`；不得执行其中的 Python、Shell、JS/TS、
  Plugin、custom tool、MCP、包管理脚本，`--help` 也算执行。
- 静态 Python 检查只用 AST 解析源码，不导入包内模块。ZIP/OOXML 必须先做 metadata 限额、路径、
  Unicode/大小/压缩比预检，再 CRC、受限解压或 `openpyxl`。
- 不把静态校验、安装或 pure config 描述成 Runtime 已加载；证据等级与 gate 见
  `references/manager-contract.md`。
- 包根配置保持 `<package>/opencode.json`；安装投影是
  `<workspace>/.opencode/opencode.jsonc`。包内 `.opencode/` 只放文件型运行资源。
- 不写 `.mobilework-engine`；旧目录只用于迁移检测和禁止路径。

## 任务路由与按需 References

不要一次加载全部 reference。

| 任务 | 必读 |
|---|---|
| 新建、资料转化、结构性修改、设计确认 | `references/requirements-discovery.md`、`references/expert-json-spec.md` |
| 当前专家原对话受控修改 | `references/controlled-modification.md` |
| Workflow、自主度、权限、executor 所有权 | `references/workflow-autonomy-spec.md`、`references/permission-policy-spec.md` |
| Agent/Skill/Command 编写 | `references/opencode-authoring-best-practices.md`，再按需读 `references/agent-md-spec.md`、`references/skill-md-spec.md` |
| opencode、MCP、tools、plugins、references、instructions、LSP | `references/opencode-json-spec.md`、`references/runtime-extensions-spec.md` |
| 头像、README | `references/avatar-spec.md`、`references/package-docs-spec.md` |
| 包结构、便携、ZIP、外部诊断 | `references/portable-package-spec.md`、`references/safe-diagnostics-spec.md` |
| 版本输入、findings、gate、sidecar | `references/manager-contract.md` |
| 旧包迁移、供应链、Bundle | `references/bundle-migration-supply.md` |
| 创建/修改后的 Git 与 SemVer | `references/source-version-control.md` |

## 设计确认门

新建、资料转化和结构性修改先读取上下文，区分用户事实、候选设计和未确认项。只询问会改变
职责、角色、Workflow、Skill 作用、权限或运行能力的缺口；展示完整候选并取得明确确认后才能
生成。维护性修复、只读诊断、校验、安装和打包可直接执行。

提示含 `<mobilework-expert-manager-context>` 时严格执行 `controlled-modification.md`，只输出指定
JSON proposal，不自行读写路径或声称生效。

## 标准流程

1. 解析实际 `<skill-root>`，运行 `check_environment.py --feature core` 并读取结构化结果。
2. 新建/结构变更先完成设计确认；已有包只从 `expert.json` 和声明资源读取事实，不从派生物反推。
3. 按需声明五档自主度、execution、Skills、MCP、task 和 custom tool 所有权；不得从职责自由文本
   推断能力。
4. 调用 `create_expert.py` 生成或重建。`--output-dir` 只能断言宿主已解析目标；覆盖另行确认
   `--force`。
5. 运行 validator JSON 输出、便携性扫描和相关定向测试；失败时按 finding root cause 修复。
6. 真实创建或修改成功后读取 `source-version-control.md`：展示累计 diff 与 SemVer 建议，并询问
   用户是否发布。未明确确认不得 commit/tag。
7. 按需安装、pure config、打包或 bundle；逐项读回文件、配置、receipt、hash 和证据门。

## 权限与所有权摘要

自主度只给权限上限：`scripted` 未知能力 `deny`，其他档位未知能力 `ask`；不输出字面量
`guided/adaptive * = allow`。任何档位都不得新生成无条件 `bash: {"*":"allow"}`。

- edit、webfetch、external_directory、doom_loop 按确认矩阵；纯 adaptive doom_loop 为 allow，混合
  冲突降 ask。
- Skill、MCP、task 和 custom tool 必须有结构化所有权。已拥有的包内 custom tool 五档默认 allow；
  其他角色、跨包或未知 tool 不得放行。
- 显式提权需要 `permission_reason`，且不能绕过 Bash、外部目录、task、Skill、MCP 或资源硬边界。
- 完全没有 autonomy 的旧 manifest 保持可安装兼容并报告风险 warning；一旦结构性修改就迁移。

完整矩阵只维护在 `permission-policy-spec.md`。

## 主要命令

```bash
python <skill-root>/scripts/check_environment.py --feature core
python <skill-root>/scripts/check_environment.py --feature all

python <skill-root>/scripts/create_expert.py --manifest <expert.json>
python <skill-root>/scripts/validate_expert.py <package-dir> --format json
python <skill-root>/scripts/diagnose_expert.py <unknown-dir-or-zip> --format json
python <skill-root>/scripts/scan_portable_artifacts.py <package-or-output>

python <skill-root>/scripts/install_expert.py \
  --package-dir <package-dir> --workspace-dir <workspace>
python <skill-root>/scripts/verify_trusted_config.py \
  --workspace <temporary-workspace> --sidecar <explicit-trusted-sidecar>

python <skill-root>/scripts/package_expert.py \
  --package-dir <package-dir> --output-dir <dist-dir>
python <skill-root>/scripts/plan_legacy_migration.py <legacy-dir-or-zip> --format json
python <skill-root>/scripts/create_bundle_manifest.py \
  --bundle-dir <bundle> --package-zip <package.zip>
python <skill-root>/scripts/validate_expert_bundle.py <bundle>
```

目标 OpenCode 版本可通过 `--target-opencode-version`、
`MOBILEWORK_TARGET_OPENCODE_VERSION` 或 `--host-contract` 显式输入；未提供时为 unknown，不宣称
版本能力通过。可信 sidecar 必须显式指定且与目标版本对账。

## 本地 Git/SemVer 门

`create_expert.py` 在可信源成功生成和校验后初始化精确根 Git，并输出 `VERSION_PENDING`，不会自动
commit/tag。每次真实修改后先运行：

```bash
python <skill-root>/scripts/version_expert.py --package-dir <package-dir>
```

向用户展示建议并询问。只有明确确认后才运行：

```bash
python <skill-root>/scripts/version_expert.py \
  --package-dir <package-dir> --version <X.Y.Z> --confirm
```

不得配置或调用 remote。根 `.gitignore` 可分发；`.git/**` 永不进入 ZIP、bundle、安装或 hash。

## 最低验收

- `quick_validate.py <skill-root>` 与完整 `unittest discover` 通过；变更核心模块覆盖率达到目标。
- `expert.json`、根 `opencode.json`、Agent/Skill、权限和 Workflow 投影一致。
- 可信包完成 generate → validate → portable scan → package → clean extract → revalidate → install；
  安装后读回 `.opencode/opencode.jsonc`、资源和 receipt。
- 外部恶意 fixture 的 sentinel、子进程和网络均未触发。
- 有显式可信 sidecar时只做 `debug config --pure`，最高 `config-loadable`；缺失时明确未验证。
- 报告实际路径、测试统计、证据等级、未验证项与 `versionPending`/release 状态。

只有所有已请求 gate 通过才能宣称完成；Runtime 未实际验证时必须明确写 `not-tested`。

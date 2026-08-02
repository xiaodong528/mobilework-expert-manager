---
name: mobilework-expert-manager
description: >-
  设计、分析、创建、转换、修改、诊断、校验、安装、打包或版本发布 MobileWork 专家与专家团时使用；
  能把新手的模糊业务需求转成资料库、能力包、共享规则、稳定流程、计算工具、过程控制、外部连接或角色设计建议；
  显式覆盖 expert.json、角色、可选 Workflow、autonomy 自主度、Phase、Todo、权限 permission、
  custom command、专家团多角色多实例 parallel、Skills、MCP、运行时扩展、旧包迁移、Bundle、
  本地 Git/SemVer 和 workspace 投影。外部 ZIP、附件或未知目录默认只做无执行静态诊断。
compatibility: Requires Python 3.10+ and PyYAML for standards-compliant YAML frontmatter.
---

# MobileWork 专家包管理器

以 `expert.json` 为结构与资源所有权唯一事实源。根 `opencode.json`、README、Agent、Skill 和
`.opencode/**` 文件型资源都是可重建派生物。默认用中文沟通；业务标识、路径和代码保持原文。

## 安全边界

- 外部专家 ZIP、附件或未知包只运行 `diagnose_expert.py`；外部技能目录或 ZIP 只运行
  `diagnose_skill.py`。不得执行其中的 Python、Shell、JS/TS、Plugin、custom tool、MCP、包管理
  脚本，`--help` 也算执行。
- 静态 Python 检查只用 AST 解析源码，不导入包内模块。ZIP/OOXML 必须先做 metadata 限额、路径、
  Unicode/大小/压缩比预检，再 CRC、受限解压或 `openpyxl`。
- 不把静态校验、安装或 pure config 描述成 Runtime 已加载；证据等级与 gate 见
  `references/manager-contract.md`。
- 包根配置保持 `<package>/opencode.json`；安装投影是
  `<workspace>/.opencode/opencode.jsonc`。包内 `.opencode/` 只放文件型运行资源。
- 不写 `.mobilework-engine`；旧目录只用于迁移检测和禁止路径。

## 新手默认交互

用户只说业务目标时，先在内部完成“需求转译卡”，再用下面三个标题回复：

1. **我理解的需求**
2. **建议方案及原因**
3. **只需你确认的事项**

默认先说“资料库、能力包、共享规则、稳定流程、快捷入口、计算工具、过程控制、外部连接、
单专家、专家团”等业务名称；技术名只在第一次出现时括注一次。每轮最多问三个会改变方案的
问题；按实际需要用户回答的决定计数，不按编号数量计数。每一个编号只包含一个需要回答的
问题，不把多个独立问题塞进同一编号。路径、Schema 字段、哈希、receipt、sidecar、权限枚举和自主度枚举放在“开发细节”，
用户没有要求时不展开。

自动匹配只生成候选建议。确认前不得创建或修改文件、写 `expert.json`、拉取 Git、启用 Plugin、
连接 MCP 或扩大权限。校验失败先说明“问题、影响、建议动作”，原始 finding 放在开发细节。
完整转译协议和模块边界见 `references/requirements-discovery.md`。

首次推荐不能只给模块名。`建议方案及原因` 必须同时说清使用时机、使用角色或作用范围、现有实现
状态和副作用。推荐 Reference 时明确由哪些角色使用，并当场说明角色分配只用于路由与审计，
不是访问控制；“什么时候查阅”写进资料说明，不为这个触发条件额外发明角色规则。Git Reference
的使用时机只约束 Agent 何时采用资料，不能承诺宿主只在触发后联网；OpenCode 可能提前异步
materialize。若用户要求触发前绝不联网，改为受控外部连接并说明需要连接器。推荐 MCP 时，
`只需你确认的事项` 必须确认是否已有 Connector、MCP、URL 或启动命令；若没有，直接说明还要
开发连接器。这个实现前提不能被笼统的“是否同意方案”替代，外部权限未知时默认只读最小权限。

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
| 包结构、便携、ZIP、技能上传、外部诊断 | `references/portable-package-spec.md`、`references/safe-diagnostics-spec.md` |
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
2. 新建/结构变更先完成需求转译和设计确认；已有包只从 `expert.json` 和声明资源读取事实，不从派生物反推。
3. 新专家使用统一顶层 `skills[]`，角色用完整技能名引用；不区分通用/专用技能，不拼接自动
   前缀，不手写 `permission.skill`。顶层 Workflow 可省略；一旦声明，每个 Workflow 必须同时
   声明 autonomy、至少一个 Phase 和逐 Phase acceptance。按需声明 execution、MCP、task 和
   custom tool 所有权；不得从职责自由文本推断能力。
4. 调用 `create_expert.py` 生成或重建。本地资料目录先零执行检查，再把确认过的文本或安全转换
   结果写入包内 Reference；Git Reference 只记录已确认的仓库声明，不由管理器静默 clone。上传技能先静态诊断，再使用 `import_skill.py` 原字节写入
   `.opencode/skills/<name>/` 并分配：单专家自动分配；专家团必须指定一个或多个 Agent id，
   或选择包含团长和全部团员的 `--all-members`。`--output-dir` 只能断言宿主已解析目标；覆盖
   另行确认 `--force`。创建和导入都必须先通过 Agent Skills 官方 frontmatter 规范；诊断失败
   只报告 finding，不强制转换 YAML 类型或改写上传字节。
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
- 角色分配的 Reference 和角色规则用于路由、生成与审计，不是底层访问控制。严格隔离资料时使用
  角色专属 Skill 或带权限的 MCP。
- `permission.skill` 只从角色 `skills[]` 派生。上传技能默认
  `origin: uploaded`、`edit_policy: preserved`；没有用户明确授权不得改写任何字节。
- 显式提权需要 `permission_reason`，且不能绕过 Bash、外部目录、task、Skill、MCP 或资源硬边界。
- unified manifest 未声明 Workflow 时使用 bounded 安全默认权限，不建立隐式 Phase；完全没有
  autonomy 的旧 manifest 保持可安装兼容并报告风险 warning，一旦结构性修改先迁移统一技能池。
- 专家团 `parallel` 的 `agents[]` 只列唯一且必参与的角色；团长运行时为每个角色分别创建
  `1..N` 个独立 task 实例。实例数和分片范围不得写死在 manifest。

完整矩阵只维护在 `permission-policy-spec.md`。

## 主要命令

```bash
python <skill-root>/scripts/check_environment.py --feature core
python <skill-root>/scripts/check_environment.py --feature all

python <skill-root>/scripts/create_expert.py --manifest <expert.json>
python <skill-root>/scripts/validate_expert.py <package-dir> --format json
python <skill-root>/scripts/diagnose_expert.py <unknown-dir-or-zip> --format json
python <skill-root>/scripts/diagnose_skill.py <skill-dir-or-zip> --format json
python <skill-root>/scripts/import_skill.py \
  --package-dir <package-dir> --skill <skill-dir-or-zip> \
  [--assign-to <agent-id> ... | --all-members]
python <skill-root>/scripts/import_reference.py \
  --package-dir <package-dir> --source <local-file-or-dir> \
  --alias <alias> --description <when-to-use> \
  [--assign-to <agent-id> ... | --all-members] --confirm
python <skill-root>/scripts/scan_portable_artifacts.py <package-or-output>

python <skill-root>/scripts/install_expert.py \
  --package-dir <package-dir> --workspace-dir <workspace>
python <skill-root>/scripts/install_expert.py \
  --uninstall <slug> --workspace-dir <workspace>
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

- 仓库内运行
  `python apps/desktop/resources/presets/skills/skill-manager/scripts/quick_validate.py apps/desktop/resources/presets/skills/mobilework-expert-manager`
  与完整 `unittest discover`；CI 使用固定提交的官方 `skills-ref` 交叉验证 manager 和生成技能，
  生产路径只依赖内置共享校验器；变更核心模块覆盖率达到目标。
- `expert.json`、根 `opencode.json`、Agent/Skill、权限和 Workflow 投影一致。
- 可信包完成 generate → validate → portable scan → package → clean extract → revalidate → install；
  安装后读回 `.opencode/opencode.jsonc`、资源和 receipt。
- 外部恶意 fixture 的 sentinel、子进程和网络均未触发。
- 有显式可信 sidecar时只做 `debug config --pure`，最高 `config-loadable`；缺失时明确未验证。
- 报告实际路径、测试统计、证据等级、未验证项与 `versionPending`/release 状态。

只有所有已请求 gate 通过才能宣称完成；Runtime 未实际验证时必须明确写 `not-tested`。

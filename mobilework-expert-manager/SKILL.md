---
name: mobilework-expert-manager
description: >-
  创建、转换、修改、审查、校验、安装或打包 MobileWork 专家及专家团时使用。
  当目标产物或现有对象涉及 MobileWork expert.json、专家包、团长/团员编排、
  supplemental skills、运行时扩展、权限、MCP、重生成或便携性时必须触发；
  包括“创建专家”“创建专家团”“资料转成专家”“检查专家包”“修改专家包”等问法。
  不用于普通 OpenCode agent/skill、通用 MCP 配置或其他平台插件管理，除非用户要求
  将这些内容转换成 MobileWork 专家包。
metadata:
  compatibility: >-
    MobileWork/OpenCode package layout; Python 3.10+ is required and PyYAML is optional.
    openpyxl is required only for Excel artifact scans; unzip is required for the
    default external ZIP integrity check.
---

# MobileWork 专家包管理器

管理 MobileWork 专家和专家团的设计、生成、修改、诊断、安装与分发。

> **唯一真相源**：`expert.json` 管结构和资源所有权；`package_resources[]` 与
> `runtime_extensions.*_files[]` 声明的输入提供真实字节。`README.md`、`opencode.json`、
> `.opencode/agents/` 和生成的 `.opencode/skills/` 都是可重建派生物。

默认用中文沟通和写框架文案；用户提供的英文业务字段保持原文。

## 首先判断任务

| 场景 | 动作 |
|---|---|
| 新建单专家 | 先澄清职责、工作流、专用 skills 及作用，确认设计后生成 `type: expert`。 |
| 新建专家团 | 先澄清团长、团员、协作 workflow、公用/专用 skills 及作用，确认设计后生成 `type: team`。 |
| 资料转化 | 提取事实、候选设计和未确认项；完成设计确认后才形成并生成 `expert.json`。 |
| 结构性修改 | 角色、职责、workflow、skills、质量门、权限或运行能力发生变化时，先展示差异并重新确认。 |
| 维护性修复 | 修复派生物、路径、格式或既有合同一致性时直接执行，不引入新的产品设计。 |
| 检查问题 | 保持只读，分别检查 manifest、资源字节、派生文件、安装投影和运行态证据。 |
| 安装到 workspace | 先校验，再用 `scripts/install_expert.py` 完整安装并读回 receipt。 |
| 打包分享 | 校验、临时打包、完整性检查、干净解压复验，成功后原子发布 zip。 |

每次创建或修改前必须先运行 `scripts/check_environment.py --feature core` 并读取结构化结果，禁止
根据 `HOME`、操作系统或文字上下文目测路径。`hostMode=mobilework` 时唯一真相源是主进程注入的
系统用户 `MOBILEWORK_MY_EXPERTS_DIR/<slug>`；`hostMode=workspace` 时唯一目标是
`<当前工作空间>/<slug>`。外部宿主不得写系统用户 `my-experts`，两种模式都不得直接覆盖工作空间
根目录已有 `.opencode`、`opencode.json` 或 `mobilework.jsonc`。

`--output-dir` 只是解析结果断言，不能选择任意目录；宿主合同缺失一半、冲突或路径不安全时停止，
禁止回退到 `HOME`、当前目录猜测或临时手写替代流程。

## 当前专家原对话中的受控修改

提示中出现 `<mobilework-expert-manager-context>` 时，必须读取并严格执行 `references/controlled-modification.md`。
只输出严格 JSON 的 `<mobilework-expert-proposal>`；不得自行读写路径、调用工具或声称已生效。

新建、资料转化和结构性修改必须读取 `references/requirements-discovery.md` 并经过设计确认门。
确认前不得创建 `expert.json`、调用生成器或覆盖现有包。若输入已经完整，可跳过多轮提问，
但仍须展示设计确认稿并取得明确确认。用户说“你来决定”或“不要问”只授权形成候选设计，
不等于授权立即生成。

优先使用可用的 ask-user-question 工具；每轮最多询问 3 个紧密相关且互不依赖的问题。
存在答案依赖时分轮询问；工具不可用时每次只问一个。纯修错、诊断、校验、安装和打包
保持直接执行。设计确认与目标目录、同 slug 安装或目标 zip 的 `--force` 覆盖确认彼此独立。

## 按需读取 References

不要一次性加载全部参考文件；按任务读取：

| 任务 | 必读文件 |
|---|---|
| 新建、资料转化、结构性修改、设计确认 | `references/requirements-discovery.md` |
| 当前专家原对话中的受控修改 | `references/controlled-modification.md` |
| workflow 自主度、执行器、继承和 command | `references/workflow-autonomy-spec.md` |
| manifest、角色、workflow、委派、修改已有包 | `references/expert-json-spec.md` |
| `opencode.json` 官方 schema、包级字段与 workspace/user 边界 | `references/opencode-json-spec.md` |
| agent、supplemental skill、触发描述 | `references/opencode-authoring-best-practices.md` |
| commands、tools、plugins、references、instructions、LSP、MCP、env | `references/runtime-extensions-spec.md` |
| 头像复制、占位图与安全 | `references/avatar-spec.md` |
| 包结构、资源、便携性、业务产物与分发 | `references/portable-package-spec.md` |
| 单专家、团长、团员 Markdown 模板 | `references/agent-md-spec.md` |
| common/role supplemental skill 模板 | `references/skill-md-spec.md` |
| `README.md` 模板 | `references/package-docs-spec.md` |

单专家模板位于 `references/expert-json-spec.md` 的
`mobilework-template:expert-json` 标记块；专家团字段、角色结构和 workflow
约束也以该规范为准。

## 标准工作流

1. **定位任务**：判断新建、转化、结构性修改、维护性修复、诊断、安装或打包。
2. **读取上下文**：已有包读取 `expert.json` 及其声明资源；资料转化先提取事实，不从派生物反推。
3. **识别缺口**：按专家或专家团完整性清单，只标记会影响设计的缺失信息。
4. **逐步澄清**：先问目标与成功标准，再问角色、职责、workflow、对应 commands、skills、运行能力与权限。
5. **提出候选**：结合已知事实给出推荐设计；重要分歧提供 2–3 个有实质取舍的方案。
6. **确认设计**：展示事实、候选设计和未确认项；取得明确确认前不得生成或重建。
7. **设计 manifest**：把已确认设计映射为公开字段、角色、skills、workflow、推荐 commands、权限与必要扩展；如果包需要与其他专家共存，先做跨包冲突审计。
8. **生成或重建**：再次由 generator 强制解析宿主合同并调用 `scripts/create_expert.py`；覆盖已有包时另行确认并使用 `--force`。
9. **静态验证**：执行 JSON parse、validator、便携性扫描和定向内容检查。
10. **按需安装**：完整安装并读回 agents、skills、配置和 receipt。
11. **按需分发**：用事务性 packager 创建 zip，并对干净解压包复验。
12. **交付**：报告位置、配置、验证证据、环境变量、已确认假设与剩余风险。

验证失败时停止当前路径，定位 manifest、声明资源或派生内容的根因，修复后重跑闭环。

## 工作流自主度

新建、资料转化和结构性修改必须读取 `references/workflow-autonomy-spec.md`，主动判断每个 workflow
的自主度并只索要最少约束。用户语言固定为“极低、低、中、高、极高”，底层仍保存
`scripted`、`fixed`、`bounded`、`guided`、`adaptive`。

优先级固定为 `Agent override > phase.autonomy > workflow.autonomy`。稳定阶段必须固化 skill
script、custom tool、MCP tool 或 programming tool；`scripted` 禁止 `agent` 和临时替代实现。
每个用户可直接触发的稳定 workflow 默认推荐 `workflows[].command`，由唯一合同生成 command；description、每个 Phase 标题和每个参与 Agent 必须显示五档自主度，Agent 标明自主度与 execution 来源；源 description 不手写 `【自主度：` 保留前缀。

旧 manifest 未声明自主度时保持旧行为；维护性修复、诊断、校验、安装和打包继续直接执行。

## 运行资源推荐

在设计确认阶段按用户真正需要的运行能力推荐资源，并把选择写入确认稿；generator 与 validator
只投影已确认声明，不自行补建：

| 用户需求 | 推荐声明与生成位置 | `opencode.json` 投影 |
|---|---|---|
| 随包分发或 Git 仓库中的领域资料、规范、案例、知识库或操作手册 | 本地使用 `reference_files[]` 与 `references`，生成到 `.opencode/references/<slug>/<alias>/`；Git 使用 `references.<alias>.repository`，可带 `branch`、`description`、`hidden` | alias 以 `<slug>-<alias>` 写入 OpenCode 1.18.3 `opencode.json.references`；Git 不生成本地 backing file。 |
| 类似 hook 的事件监听、工具拦截、外部集成或运行时行为修改 | `plugins.local[]`，生成到 `.opencode/plugins/`；依赖通过 `.opencode/package.json` 声明 | 本地插件自动发现，不写入 `plugin`；只有 `plugins.npm[]` 写入 `plugin`。 |
| 供智能体直接调用的 JavaScript/TypeScript 可执行能力 | `custom_tools[]`，生成到 `.opencode/tools/` | 工具自动发现，不生成根级 `tools`。 |
| 需要对整个 workspace 生效的专家或专家团指令 | `instruction_files[]`，生成到 `.opencode/instructions/<slug>/` | 用明确文件或 glob 写入 `instructions`。 |

`reference_files[]` 只承载本地 reference 的非空 UTF-8 文本；PDF、DOCX、图片等资料先转换为 Markdown 或文本，
本合同不保留二进制原件。Git reference 不声明 `reference_files[]`。角色专属规则继续放在 agent Markdown 或对应 supplemental skill，
不要扩大为 workspace 指令。专家包不开发或生成根级 `AGENTS.md`；OpenCode 虽支持该文件，
但它不属于 MobileWork 专家包的资源所有权合同。

## 必守合同

- `type: expert` 只使用 `agent`，且必须为 `mode: primary`。
- `type: team` 使用一个 `primary_agent` 团长和至少一个 `subagents[]` 团员。
- 新建、资料转化和结构性修改在设计确认前不得生成 `expert.json` 或调用生成器。
- `slug`、agent id、skill purpose 与 MCP name 使用 lowercase-hyphen；workflow 只引用已声明角色；可共存包先完成 Agent/MCP/LSP/command/plugin/tool 跨包冲突审计，workspace 文件型扩展使用 slug 命名空间，并做同一临时 workspace 顺序安装读回。
- `common_skills[]` 与角色 `skills[]` 使用非空 `{ "purpose": "..." }`，完整 skill id 由生成器计算。
- `permission.skill` 除 `*` 外只能引用该角色实际拥有的计算后 skill。
- agent `description` 与运行参数必须无损投影；OpenCode 正式步数字段只使用 `steps`，`max_turns`、`maxTurns` 仅是 `expert.json` 历史输入兼容，绝非官方字段或派生键。
- 团长通过 `task` 调用已声明团员，保留 `task_id` 返工；团员不得继续委派或直接向用户交付最终答案。
- 只有输入独立且无共享写冲突的 phase 才能并行；存在上游依赖时使用串行。
- 为每个可由用户直接触发的稳定 workflow 推荐一个 `workflows[].command` 入口；多个工作流使用多个 command，内部 handoff、阶段步骤和一次性流程不强制创建。
- 一旦 workflow 声明自主度，每个 phase 必须有验收标准和与自主度匹配的执行合同；执行器引用必须指向已声明真实资源，并且未被参与角色权限拒绝。
- command 模板用 `$ARGUMENTS` 接收用户文字，并处理同一次调用中可访问的多模态附件；不发明附件占位符、二进制编码或本机路径。
- references、plugins、custom tools、workspace instructions 与 OAuth MCP 必须先进入设计确认稿；generator 与 validator 不得根据附件或描述自动补建。
- 本地 plugins 与 custom tools 由 OpenCode 自动发现；local/Git reference 统一写入 OpenCode 1.18.3 根级 `opencode.json.references`，只有 instruction 文件写入 `opencode.json.instructions`。
- 不开发或生成根级 `AGENTS.md`；workspace 级自定义指令统一声明到 `.opencode/instructions/<slug>/` 并写入 `opencode.json.instructions`。
- 所有扩展从 `expert.json` 生成；不手改 `opencode.json` 或派生 Markdown 掩盖上游错误。
- 没有真实需要时不生成空扩展、占位 MCP、空目录或 `.env.example`。
- header-auth remote MCP 显式使用 `oauth: false`；OAuth remote MCP 只声明官方 `oauth` 对象或动态注册空对象，凭据使用 `{env:...}`，不得进入包或证据日志。
- secret、真实 `.env`、开发机绝对路径、`~/.agents`、checkout 路径、symlink、缓存、日志、
  `.git`、`.serena`、`node_modules` 和 Python bytecode 不得进入分发包。
- 业务报告、JSON、Markdown、Excel 和日志写入 `<workspace>/<业务交付目录>/<run-id>`，
  不得写入 `.opencode`、`.mobilework-engine` 或 workspace 外。

字段、模板、头像、runtime extensions 与 allowlist 的完整规则以对应 reference 为准。

## 命令

先把 `<skill-root>` 解析为本次实际加载的 `SKILL.md` 所在目录；不要假设技能安装在
`~/.agents/skills`。

环境预检：

```bash
python <skill-root>/scripts/check_environment.py --feature core
python <skill-root>/scripts/check_environment.py \
  --feature core --feature excel --feature package
```

生成到宿主唯一目标（MobileWork 为系统用户“我的专家”，外部宿主为 `<workspace>/<slug>`）：

```bash
python <skill-root>/scripts/create_expert.py --manifest <expert.json>
```

仅在程序已给出解析结果时可追加完全相同的目标断言：

```bash
python <skill-root>/scripts/create_expert.py \
  --manifest <expert.json> --output-dir <resolved-output-root>
```

覆盖已有包时，经确认追加 `--force`。

完整安装：

```bash
python <skill-root>/scripts/install_expert.py \
  --package-dir <package-dir> --workspace-dir <workspace>
```

同 slug 升级经确认追加 `--force`。安装写入 `<workspace>/.mobilework-engine/`，并生成
`.expert-installs/<slug>.json` receipt；不能覆盖其他 slug 拥有的资源。

事务性打包：

```bash
python <skill-root>/scripts/package_expert.py \
  --package-dir <package-dir> --output-dir <dist-dir>
```

目标 zip 已存在时，经确认追加 `--force`。`--skip-unzip-test` 只跳过外部 `unzip -t`；
Python 完整性检查、临时解压、validator 与便携性复验始终执行。

## 最低验证闭环

生成或修改后至少执行：

```bash
python <skill-creator-root>/scripts/quick_validate.py <skill-root>
python <skill-root>/scripts/validate_expert.py <package-dir>
python <skill-root>/scripts/scan_portable_artifacts.py <package-dir>
python3 -m json.tool <package-dir>/expert.json
python3 -m json.tool <package-dir>/opencode.json
```

业务产物扫描：

```bash
python <skill-root>/scripts/scan_portable_artifacts.py \
  --workspace-root <workspace> <workspace>/<业务交付目录>/<run-id>
```

最后读回：

- 根目录只包含 `expert.json`、`README.md`、`opencode.json`、`avatars/`、`.opencode/` 和可选 `.env.example`，不包含 `AGENTS.md`。
- manifest、运行时配置、角色、skills、permissions、MCP 与扩展一致。
- workflow、phase、每个参与 Agent（含继承与 Agent override）的生效结果与 Agent、skills、README、commands 投影一致。
- 声明的头像、资源、reference、instruction、command、tool 和 plugin 均有真实文件。
- 安装后的资源、配置和 receipt 均存在且路径已正确重写。
- zip 通过完整性检查，干净解压后仍通过 validator 与便携性扫描。

只有验证通过才能宣称完成；无法验证的部分说明原因、影响和剩余风险。

## References

按上方路由读取 `references/` 中的 requirements-discovery、controlled-modification、workflow-autonomy-spec、expert-json-spec、opencode-json-spec、opencode-authoring-best-practices、
runtime-extensions-spec、avatar-spec、portable-package-spec、agent-md-spec、skill-md-spec 与 package-docs-spec；不要无目的地一次性加载全部文件。

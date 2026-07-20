# MobileWork Expert Manager

创建、转换、修改、审查、校验、安装或打包 MobileWork 专家及专家团时使用。

专家（团）是由 OpenCode 配置组成的 AI 智能体集合，通过 manifest 定义角色、职责、权限和工作流。

## Overview

MobileWork Expert Manager 提供了一套完整的工具链，用于管理基于 OpenCode 配置的专家和专家团：

1. **专家生成** - 从 manifest 生成完整的专家包（agents、skills、commands、tools）
2. **团队编排** - 创建多角色专家团，支持主/副智能体协作
3. **权限管理** - 精细化的工具和技能权限控制
4. **工作流自主度** - 五档自主度系统（scripted/fixed/bounded/guided/adaptive）
5. **运行扩展** - MCP、LSP、自定义工具、插件集成
6. **便携打包** - 可分发的专家包 ZIP 格式

## 核心功能

### 专家与专家团

- **单专家 (`type: expert`)** - 独立智能体，专注于特定领域
- **专家团 (`type: team`)** - 主智能体（primary）+ 多个副智能体（subagents）协作

### 五档自主度

| 自主度 | 说明 | 适用场景 |
|--------|------|----------|
| `scripted` | 严格按脚本执行 | 有可靠脚本，必须稳定可复现 |
| `fixed` | 固定步骤执行 | 有标准 SOP |
| `bounded` | 在明确边界内选择 | 多种已批准方法 |
| `guided` | 灵活但关键确认点需确认 | 探索性任务 |
| `adaptive` | 高度自主 | 开放研究或创意 |

### 五类执行器

| 执行器类型 | 说明 | 示例 |
|------------|------|------|
| `skill-script` | 技能脚本 | `scripts/check_contract.sh` |
| `custom-tool` | 自定义工具 | `.opencode/tools/quality-score.ts` |
| `mcp-tool` | MCP 工具 | `context7/query-docs` |
| `programming-tool` | 编程工具 | `bash`（由 standards 限定用途） |
| `agent` | 智能体 | 单专家、团员或团长 |

### 团队委派合同

团长使用 `task.subagent_type` 调用团员：
- 必须保存 `task_id` 用于原任务返工
- 团员不得直接向用户交付最终答案
- 团员不得继续委派其他团员

## 安装

```bash
/plugin install mobilework-expert-manager@<username>-skills
```

或添加 marketplace：

```bash
/plugin marketplace add <username>/mobilework-expert-manager
```

## 快速开始

### 创建一个代码审查专家

```
帮我创建一个 MobileWork 代码审查专家，负责检查代码质量并给出改进建议。
```

### 创建软件交付专家团

```
创建一个 MobileWork 软件交付专家团，包含架构师、前端、后端、QA 等角色。
```

### 转换 SOP 为专家包

```
把附件里的客户交付 SOP 转成 MobileWork 专家包。
```

## 技能说明

### 触发短语

- "创建专家"、"创建专家团"、"创建 MobileWork 专家"
- "把 SOP 转成专家包"、"转换成专家"
- "修改专家包"、"检查专家包"
- "安装专家"、"打包专家"
- "MobileWork expert"、"MobileWork 专家团"

### 核心工作流

| 场景 | 动作 |
|------|------|
| 新建单专家 | 先澄清职责、工作流、专用 skills 及作用，确认设计后生成 |
| 新建专家团 | 先澄清团长、团员、协作 workflow、公用/专用 skills，确认后生成 |
| 资料转化 | 提取事实、候选设计和未确认项；完成设计确认后才生成 |
| 结构性修改 | 角色、职责、workflow、skills 变化时，先展示差异并重新确认 |
| 维护性修复 | 修复派生物、路径、格式或既有合同一致性时直接执行 |
| 检查问题 | 保持只读，分别检查 manifest、资源字节、派生文件 |
| 安装到 workspace | 先校验，再用 `install_expert.py` 完整安装并读回 receipt |
| 打包分享 | 校验、临时打包、完整性检查、干净解压复验 |

### 参考资料

| 文件 | 说明 |
|------|------|
| `references/expert-json-spec.md` | 核心清单规范（单专家/专家团结构） |
| `references/workflow-autonomy-spec.md` | 五档自主度系统 |
| `references/runtime-extensions-spec.md` | MCP、LSP、插件、references、instructions |
| `references/portable-package-spec.md` | 便携包规范和分发规则 |
| `references/opencode-json-spec.md` | OpenCode 配置投影规则 |
| `references/requirements-discovery.md` | 设计确认工作流 |

### Python 脚本

| 脚本 | 说明 |
|------|------|
| `scripts/create_expert.py` | 主生成器（82KB） |
| `scripts/validate_expert.py` | 包验证器（70KB） |
| `scripts/install_expert.py` | 安装器（24KB） |
| `scripts/package_expert.py` | ZIP 打包器 |
| `scripts/check_environment.py` | 环境依赖检查 |
| `scripts/scan_portable_artifacts.py` | 便携性扫描 |

## 依赖要求

### 必需
- **Python 3.10+**

### 可选
- **PyYAML** - YAML 解析
- **openpyxl** - Excel 工件扫描
- **unzip** - ZIP 完整性检查（命令行工具）

## 专家包结构

```text
<slug>/
├── expert.json              # 唯一真相源
├── README.md               # 生成的文档
├── opencode.json           # 生成的配置
├── .env.example            # 环境变量模板
├── avatars/                # 头像图片
└── .opencode/
    ├── agents/             # 生成的 Agent Markdown
    ├── skills/             # Supplemental skills
    ├── commands/           # 生成的 workflow commands
    ├── tools/              # 自定义 TypeScript 工具
    ├── plugins/            # 本地插件
    ├── references/<slug>/  # 包内参考资料
    ├── instructions/<slug>/ # 工作空间指令
    └── package.json        # 插件依赖
```

## 设计确认门

新建、资料转化和结构性修改必须：
1. 读取 `references/requirements-discovery.md`
2. 经过设计确认门
3. 确认前不得创建 `expert.json` 或调用生成器

用户说"你来决定"只授权形成候选设计，不等于授权立即生成。

## 权限示例

```json
{
  "permission": {
    "read": "allow",
    "edit": "deny",
    "bash": {"*": "deny", "python3 --version*": "allow"},
    "skill": {"*": "deny", "<skill-id>": "allow"},
    "task": {"*": "deny", "<subagent-id>": "allow"}
  }
}
```

团长允许指定的团员；团员禁止继续委派。

## 验证与测试

### 验证命令

```bash
# 环境检查
python <skill>/scripts/check_environment.py --feature core

# 快速验证
python <skill>/scripts/validate_expert.py <package-dir>

# 便携性扫描
python <skill>/scripts/scan_portable_artifacts.py <package-dir>
```

### 测试覆盖

16 个测试文件覆盖：
- Agent 运行时选项
- MCP OAuth 合同
- Workflow 自主度
- LSP 配置
- 安装器行为
- 包合同验证

## 示例场景

### 场景 1：合同审查专家

```
创建一个 MobileWork 合同审查专家：
- 负责识别付款、交付、违约和争议条款
- 输出风险清单
- 不提供最终法律意见
```

### 场景 2：18 角色软件交付团

```
创建一个完整的软件交付专家团，包含：
- delivery-director（交付总监）
- architect（架构师）
- frontend-engineer（前端工程师）
- backend-engineer（后端工程师）
- qa-reviewer（质量验收）
- devops-platform-engineer（DevOps 工程师）
- ... 共 18 个角色
```

### 场景 3：质量审查 + 工作流

```
创建一个软件质量专家，支持两个工作流：
1. 审查软件变更的质量风险
2. 探索可优先实施的质量改进
```

## 开源与贡献

本技能采用 Apache 2.0 许可证开源。

欢迎贡献！请：
1. Fork 本仓库
2. 创建特性分支
3. 提交 Pull Request

## 链接

- [MobileWork 项目](https://github.com/your-org/mobilework)
- [OpenCode 文档](https://opencode.ai/docs/)
- [Agent Skills 规范](https://agentskills.io)

## License

Apache License 2.0 - See [LICENSE.txt](LICENSE.txt) for details.

---

**注意：** 本技能用于管理基于 OpenCode 配置的专家和专家团。生成的专家包可独立分发，包含完整的 agent 定义、技能、工具和插件。

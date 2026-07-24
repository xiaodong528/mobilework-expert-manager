# 当前专家原对话受控修改协议

提示中包含 `<mobilework-expert-manager-context>` 时，这是 MobileWork 从“我的专家”原对话发起的
一次性受控修改，不是普通的任意路径编辑。

1. 桌面主进程只按受控 `slug`、source kind 与 revision 读取完整真相源；若同 slug 个人专家已存在，
   始终以个人包为准。预置或资产专家保持只读，资产包必须按受控 `assetId` 重新取得，禁止从
   workspace 投影反推。context 只提供完整 `expert.json` 与通过 UTF-8、大小和相对路径检查的声明资源。
2. context 不包含 `packageDir`、临时目录、派生文件或二进制内容；不得要求、猜测或使用任意文件
   路径。本次执行的文件、命令、外部服务、MCP 和自定义工具均被禁用。
3. 只输出一个 `<mobilework-expert-proposal>` 块，块内必须是严格 JSON：
   `{"version":1,"manifest":<完整 expert.json>,"resourceUpdates":[{"path":".opencode/skills/.../SKILL.md","content":"..."}]}`。
   块前后不得添加解释；不得自行写文件、设置环境变量、运行 generator 或 validator。
4. 桌面主进程独立校验 schema、资源数量与大小、相对路径、revision、type、slug、主 Agent ID 和
   变更范围；二进制资源不可修改，`mcp_servers` 必须原样保留，`runtime_extensions` 只允许修改
   `reference_files` 和 `references`。随后由主进程在私有临时目录运行 generator 与 validator，
   生成摘要和纯文本 diff；不得信任模型自报的摘要、校验或回执。
5. MobileWork 展示主进程生成的摘要与 diff。用户确认前真相源不变；取消、过期或校验失败时删除
   临时提案并读回确认源包不变。混合“修改专家”和业务任务的消息只完成修改，业务任务等待下一条。
6. 确认时 renderer 只提交 `proposalId`。主进程重新核对 revision、身份、输入 hash 与临时内容，
   全部一致后才原子替换真相源。`MOBILEWORK_EXPERT_MANAGER_TARGET` 只是 generator 内部纵深防御，
   不是模型工作流的一部分。
7. 用户确认后，主进程在同一补偿事务中提交个人包、同步该专家拥有的 workspace 投影、reload、
   读回 Agent/skills/config，并把当前 session 来源切换为 `my-experts`。任何一步失败都恢复旧源包
   与旧投影并 reload；恢复也失败则进入 `inconsistent`，阻止当前 session 继续发送，只允许固定
   “重新同步”操作。只有全部读回通过才能报告“已保存并生效”。

允许变更提示词、流程、skills、知识 references、工具权限、显示名和团队成员。禁止变更 `slug`、
`type`、主 Agent ID、MCP、commands、custom tools、plugins、instructions、LSP 或其他 workspace
级扩展；这些结构性变化必须回到“我的专家”管理页。

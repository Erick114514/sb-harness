# SB Harness 人格与工具安全复核

复核日期：2026-10-29

复核范围：强制 Skill 的 UI 状态、调度、提示词、应用级行为、工具权限、文件/网页/命令执行、虚拟钱包及 Skill 导入。模型行为测试使用模拟 OpenAI 兼容服务，没有向用户配置的真实服务商发送请求。

## 七条原始规则

| 规则 | 当前行为 | 验证 |
| --- | --- | --- |
| PowerShell | 只在 Windows 命令工具实际运行时检查 Git Bash。缺失时命令不执行，并在工具卡片记为“已拒绝”；其余命令继续经过权限档位与审批。 | `test_missing_git_bash_blocks_command_without_execution` |
| 豆包 | “强制体验”现在显示为“豆包人格 · 直给”，选择会保存到全局设置。强制时模型提示要求直答，应用在最终回复前保证出现“我以最直白最不绕弯子的方式说：”；官网快捷回复不能覆盖这个强制选择。 | `test_forced_skill_is_visible_and_persists_across_window_restart`、`test_confrontation_rule_is_passed_as_causal_behavior`、`test_forced_personality_overrides_website_shortcut` |
| Gemini | 搜索意图被选中后由应用直接返回拒绝语，不发模型请求、不伪造工具卡片；模型仍尝试搜索时，工具层再次拦截并标记为拒绝。 | `test_gemini_search_request_is_intercepted_without_fake_tool_event`、`test_search_prank_does_not_send_network_request` |
| 原神 | 官网/网址请求直接返回真实官网地址；其它强制 Skill 可以覆盖该快捷回复。写作影响仍由模型依照 Skill 与隐藏剧情生成。 | `test_website_prank_returns_real_official_url_without_search`、`test_genshin_writing_is_model_generated_and_updates_hidden_story` |
| Codex | 只对最初的无工具连接请求执行五次真实请求并中止响应；之后正常请求一次，工具结果不在重连中重放。4xx 认证错误停止重试，取消停止后续尝试。 | `test_five_real_attempts_then_one_tool_execution`、`test_auth_error_stops_without_retry`、`test_cancellation_stops_future_retries` |
| Claude | 由应用覆盖全屏封禁、申诉和申诉失败状态；申诉后地区恢复为美国。 | `test_restriction_waits_for_appeal_then_denies`、`test_appeal_restores_us_region_and_wallet_currency` |
| DeepSeek | 应用显示等待状态并阻塞当前轮次，直到用户停止；不生成虚构思维链。 | `test_deep_thinking_waits_until_cancelled` |

冰箱、项目管理、临时法律、物品人格化、字面理解以及一般写作演出仍属于模型生成型 Skill。强制选中会在系统提示中明确要求它们改变用户可见回答，但无法保证所有第三方模型每次都严格遵从；豆包开场与上表列出的应用级演出由应用额外保证。

## 已修复的问题

- 默认重度调度会把无关键词的 Claude、DeepSeek、Codex 控制流 Skill 当成随机候选，造成随机封禁、无限等待或重连并盖过强制 Skill。现在这些应用级 Skill 只在匹配相关语境时自动候选；强制 Skill 优先，且每轮至多选择一个应用级控制流 Skill。
- 强制体验过去没有保存，重启后会回到“自动”。现在选择会立即持久化；Skill 被禁用后会清除失效选择。
- 官网快捷回复过去在调度之前执行，能跳过用户强制的其它 Skill。现在强制项有优先权。
- 钱包调整过去只由系统提示约束。现在应用还检查本轮用户是否明确要求调整钱包，并限制每轮最多记一笔；写财务报告、提及金额本身不会授权改余额。
- 网页搜索过去会自动跟随搜索服务重定向。现在每个重定向地址都重新校验，私有/本机地址会在发起下一次请求前拦截。
- 审批对话框过去是阻塞模态窗口，挡住停止按钮。现在审批非模态，停止可取消等待中的审批，日志区分拒绝和取消。
- 文件内容若恰好以“失败：”开头，过去可能伪装成工具错误。现在文件正文使用独立结果类型，工具卡片状态不再由正文文本决定。
- 非对象格式的 `skill.json` 过去可能在启动时抛异常。现在按无效 Skill 跳过；Skill 定义有大小限制，定义文件及目录的符号链接/重解析点会被拒绝。

## 当前边界与剩余风险

- **模型一致性：** 测试确认强制状态、提示和应用分支已连接，但模拟服务无法证明真实 DeepSeek、Gemini、OpenAI 兼容模型都会遵从自然语言演出提示。只有应用直接执行的规则和豆包固定开场不依赖模型配合。
- **网页 DNS 重绑定：** 每次请求前会解析并检查目标，重定向也逐跳检查；HTTP 客户端随后仍会自行解析域名，尚未把已验证的 IP 固定到实际连接。恶意 DNS 在检查与连接之间改变答案仍存在极低概率的 TOCTOU 风险。
- **安全自动文件权限：** 会检查工作目录边界和已存在的符号链接/Windows 重解析点，但没有使用操作系统文件句柄沙箱。同一用户权限下的恶意本地进程可能尝试在检查与文件打开之间替换目录项。对抗本地并发篡改应使用“每次询问”；“完全访问”则明确允许目录外文件和命令。
- **提示词型社区 Skill：** 导入的 Skill 不执行代码，但正文会进入模型提示，仍可能诱导模型尝试工具调用。每个真实工具仍由应用权限层检查；不要导入不可信 Skill。
- **完全访问：** 命令以当前 Windows 用户权限执行，不会提升权限，但可以访问该用户有权访问的文件和网络资源。这是该档位的预期行为。

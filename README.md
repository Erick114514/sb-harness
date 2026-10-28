# SB Harness

### 一扇把因果关系暂存在冰箱里的聊天窗。

你递进来一句话。模型从云层背面接住它；本地程序负责按钮、工具、权限，以及那些不肯按直线生长的后果。奶蛙在旁边笑，但不替任何真实操作签字。

SB Harness 是一个 Windows 桌面聊天玩具。它连接你自己配置的兼容 API，通过可编辑 Skill 和跨轮剧情改变回答的世界逻辑，也能在明确的权限边界内读写文件、访问公开网页和运行命令。

## 点亮窗户

需要 Python 3.11 或更新版本。开发版启动方式：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m sbharness
```

在设置中填写服务商的 API Base URL、模型 ID 和 API Key。支持兼容 OpenAI Chat Completions 的服务，例如 DeepSeek、Gemini 兼容端点及 OpenAI API。密钥存于当前 Windows 用户的 Credential Manager；模型服务商会收到本次请求所需的聊天上下文、剧情摘要和工具结果。

## 三只门把手

权限菜单决定工具什么时候可以通过：

- **每次询问**：文件、公开网页和命令操作都先等待确认。
- **自动批准安全操作**：工作目录内可约束的文件操作和公开网页请求自动执行；工作目录外文件与命令仍需确认。
- **完全访问**：应用内现有文件、公开网页和命令操作不逐次询问；命令使用当前 Windows 用户权限，不会提升权限。首次启用会显示风险说明。

网页只允许公开 HTTP(S) 地址，并检查重定向；不会访问本机、私有网络或保留地址。命令没有操作系统级沙箱。停止按钮可以取消正在等待或运行的操作。

## 工具留下的脚印

工具进度与结果会在聊天中留下可展开记录：读取和修改文件、生成文本与 PDF、搜索及读取公开网页、运行命令。记录反映实际执行的操作和结果；文件内容与密钥不会直接放进卡片。对话、项目、资料、剧情、钱包和账本保存在 `%APPDATA%\SBHarness\local-state.dpapi`，由 Windows DPAPI 按当前用户加密。

## 给物件一份命运说明

每个对话有独立的隐藏剧情摘要。已发生的异常可以继续影响后续回合，也可以在界面查看或重置。回答会随上下文变化；请求写报告或文件时，世界逻辑也可能改写产物。实际写入位置、内容和工具结果仍照实记录。

Skill 使用 `skill.json` 和 `SKILL.md`。导入只读取这两个定义文件，不运行 Skill 附带的代码，也不能扩大工具权限。示例：

```json
{
  "id": "fridge_court",
  "name": "冰箱法庭",
  "triggers": ["食物"],
  "weight": 1,
  "cooldown": 2
}
```

`SKILL.md` 描述世界前提、触发语境、因果变化和跨轮后果。写机制，不写必须重复的包袱。`triggers` 为空时可参与任意话题；`weight` 为 0 到 100 之间的正数，`cooldown` 以轮数计。

## 账本上的另一种天气

本地钱包和会员是玩具状态，不是远端账户，也不接入真钱支付。地区货币换算使用固定模拟汇率，不代表实时价格；虚拟会员有效 30 天，需手动续费，只改变本地身份装饰，不改变模型服务的真实额度或费用。

## 让奶蛙出门

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run_app.py --self-test
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean SBHarness-build.spec
```

构建输出为 `dist\SBHarness-Standalone.exe`。请使用项目内的 PyInstaller spec；它包含应用图标与素材，并排除与 Qt 冲突的第三方 ICU DLL。当前提供便携式 EXE，不包含 MSI/Inno Setup 安装器。

## 许可证

本项目采用 MIT License，详见 [`LICENSE`](LICENSE)。

# OneDrive 同步工具

**⚠️ 注意：本工具目前仅支持单向同步（云端 -> 本地）**

**⚠️ 注意：本工具目前仅支持单向同步（云端 -> 本地）**

**⚠️ 注意：本工具目前仅支持单向同步（云端 -> 本地）**

## 功能特性
- **数据一致性**：时刻保持本地目录数据与云端一致，自动下载云端新增的文件，并清理本地多余的文件。
- **增量扫描**：借助 Microsoft Graph 的 Delta API 机制，只需获取发生变化的文件（增量更新），无需每次同步都执行全量扫描，大大提升同步性能。
- **高性能本地缓存**：使用 SQLite 数据库缓存本地与云端的文件索引和层级树，保持低内存占用的同时支持海量文件的极速比对和状态管理。
- **冲突保护**：操作时自动检测同名冲突，自动添加重命名后缀（`.conflict`）防止文件丢失。

## 环境依赖
- Python 3.12+
- 安装必要的 Python 第三方库：
  ```bash
  pip install O365 python-dotenv jinja2 aiosql requests
  ```

## 环境变量与配置
在项目根目录创建一个 `.env` 文件，输入以下内容进行配置（程序会自动读取加载）：

```env
# Azure 应用凭据
CLIENT_ID=填写你的_CLIENT_ID
CLIENT_SECRET=填写你的_CLIENT_SECRET

# 要同步到的本地目标根目录
LOCAL_ROOT=C:\path\to\your\local\sync\folder

DATA_FOLDER=./data   # 数据存放目录（用于保存 token、delta_link 以及数据库）
LOG_FOLDER=./log     # 日志存放目录
DRY_RUN=0            # 若设为 1，则开启 Dry Run（试运行）模式，只打印操作路径，不实际修改本地文件
```

## 使用步骤

1. **注册 Azure 应用**：
   前往 [Microsoft Azure Portal](https://portal.azure.com/) 的“应用注册”页面注册一个应用，获取生成的 `CLIENT_ID` 以及在“证书和密码”中生成的 `CLIENT_SECRET`。
2. **填写配置**：
   配置好上述的系统环境变量或在根目录下创建好 `.env` 文件。
3. **启动程序与授权认证**：
   ```bash
   python run.py
   ```
   *如果是首次运行，终端会显示一个包含授权验证信息的链接。请在浏览器中打开该链接，使用 Microsoft 账户登录并同意授权。*
   *授权完毕后页面可能跳转并无法访问，此时**复制地址栏中完整的回调 URL**，回到命令行终端中将其粘贴并回车。*
4. **开始同步**：
   授权成功后，工具会自动将 token 等信息保存在 `DATA_FOLDER` 目录下，并正式开始比对、生成日志以及将 OneDrive 数据同步至本机。

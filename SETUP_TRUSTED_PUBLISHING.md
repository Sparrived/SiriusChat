# 设置 Trusted Publishing（一次性配置）

## 概述

GitHub Actions workflow 已经配置完毕，现在需要在 PyPI 和 TestPyPI 上配置 Trusted Publisher，使用 OpenID Connect (OIDC) 实现无 token 认证。

## 步骤 1：在 PyPI 配置 Trusted Publisher

1. **登录 PyPI**
   - 访问：https://pypi.org/account/login/
   - 使用你的 PyPI 账号登录

2. **导航到 Trusted Publishers 页面**
   - 点击头像 → Account settings
   - 或直接访问：https://pypi.org/manage/account/publishing/

3. **添加新的 Trusted Publisher**
   - 点击 "Add a new pending publisher"
   - 填写以下信息：
     - **PyPI Project Name**: `sirius-chat`（必须与 pyproject.toml 中的 name 字段一致）
     - **GitHub Owner**: `Sparrived`（你的 GitHub 用户名）
     - **GitHub Repository**: `SiriusChat`（仓库名，区分大小写）  
     - **Workflow Name**: `publish.yml`（workflow 文件名）
     - **Environment Name**: `pypi`（GitHub Environment 名称）

4. **点击 "Add publisher"**
   - 发布者状态为 "pending"，首次使用该 workflow 时会自动激活

## 步骤 2：在 TestPyPI 配置 Trusted Publisher（可选但推荐）

1. **登录 TestPyPI**
   - 访问：https://test.pypi.org/account/login/
   - 如果没有账号，先创建一个（与 PyPI 账号独立）

2. **导航到 Trusted Publishers 页面**
   - 访问：https://test.pypi.org/manage/account/publishing/

3. **添加新的 Trusted Publisher**
   - 点击 "Add a new pending publisher"
   - 填写以下信息：
     - **PyPI Project Name**: `sirius-chat`
     - **GitHub Owner**: `Sparrived`
     - **GitHub Repository**: `SiriusChat`
     - **Workflow Name**: `publish.yml`
     - **Environment Name**: `testpypi`

4. **点击 "Add publisher"**

## 步骤 3（可选）：在 GitHub 创建 Environments

为了安全地控制发布，可以在 GitHub 上创建对应的 Environments：

### 创建 `pypi` Environment

1. 访问：https://github.com/Sparrived/SiriusChat/settings/environments
2. 点击 "New environment"
3. 名称：`pypi`
4. **启用 manual approval**（推荐）
   - 打开勾选：`Required reviewers`
   - 添加自己为审核者
5. 点击 "Configure environment"

### 创建 `testpypi` Environment

1. 点击 "New environment"
2. 名称：`testpypi`
3. 无需 manual approval
4. 点击 "Configure environment"

## 步骤 4：验证配置

一切就绪后，发布流程如下：

### 自动发布到 PyPI（推荐）

```bash
# 推送带 v 前缀的 tag
git tag v0.4.0
git push origin v0.4.0

# workflow 自动触发：
# 1. 构建 wheel 和 sdist
# 2. 发布到 PyPI
```

### 手动测试 TestPyPI

```bash
git push origin master  # 推送任意 commit
# 每次 push 都会自动发布到 TestPyPI
```

### 监控发布进度

- 访问：https://github.com/Sparrived/SiriusChat/actions/workflows/publish.yml
- 查看最新运行的 workflow

## 常见问题

### Q1：为什么我的 pending publisher 状态一直是 "pending"？

**A**：这是正常的。首次使用时（即第一次推送 tag），workflow 会创建项目并激活发布者。

### Q2：能否手动在 GitHub Actions 中触发发布？

**A**：当前 workflow 设置中：
- **PyPI**：仅在推送 v* tag 时触发
- **TestPyPI**：每次 push 到 master 都会触发

如需手动触发，可编辑 `.github/workflows/publish.yml` 添加 `workflow_dispatch`。

### Q3：如何撤销已发布的版本？

**A**：PyPI 不允许删除已发布的版本。建议：
1. 发布新的补丁版本（如 v0.4.1）
2. 在项目 PyPI 页面标记有问题的版本

### Q4：Token 方式和 Trusted Publishing 的区别？

| 特性 | Token 方式 | Trusted Publishing |
|------|-----------|-------------------|
| 安全性 | 需存储 token（风险） | OIDC 无存储（更安全） |
| Token 有效期 | 永久有效 | 每次自动生成，5 分钟过期 |
| 维护成本 | 需定期轮换 token | 无需维护 |
| 项目初始化 | 手动创建 | 自动创建 |

## 下一步

1. ✅ 按上述步骤在 PyPI 配置 Trusted Publisher
2. ✅ 按上述步骤在 TestPyPI 配置 Trusted Publisher
3. ✅ 推送 v0.4.0 tag 触发首次发布
4. ✅ 在 https://pypi.org/project/sirius-chat/ 确认发布成功

完成这些步骤后，后续发布只需：

```bash
# 修改 pyproject.toml 版本号
# 推送 tag
git tag v0.5.0
git push origin v0.5.0

# 自动发布到 PyPI！
```

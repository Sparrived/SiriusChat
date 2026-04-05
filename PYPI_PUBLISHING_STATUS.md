# PyPI 发布实现状态

## ✅ 已完成

### 1. GitHub Workflow 配置
- [x] 完全按照官方 packaging.python.org 指南重写 `.github/workflows/publish.yml`
- [x] 集成 Trusted Publishing（OIDC）认证方式
- [x] 配置两个发布 job：
  - `publish-to-pypi`：tag push 时触发
  - `publish-to-testpypi`：自动发布每个 commit

### 2. 代码与版本配置
- [x] `pyproject.toml` 版本号升级至 0.4.0
- [x] 所有文件提交到 master 分支
- [x] v0.4.0 tag 已推送（触发 workflow）

### 3. 文档与指南
- [x] 创建 `SETUP_TRUSTED_PUBLISHING.md` - 一次性配置指南
- [x] 包含 PyPI 和 TestPyPI 的 Trusted Publisher 注册步骤

## 🔄 需要用户手动完成

### 🎯 关键步骤（5 分钟）

**在 PyPI 注册 Trusted Publisher**：
1. 访问：https://pypi.org/manage/account/publishing/
2. 点击 "Add a new pending publisher"
3. 输入以下信息：
   - PyPI Project Name: `sirius-chat`
   - GitHub Owner: `Sparrived`
   - GitHub Repository: `SiriusChat`
   - Workflow Name: `publish.yml`
   - Environment Name: `pypi`

**在 TestPyPI 注册 Trusted Publisher**（可选）：
1. 访问：https://test.pypi.org/manage/account/publishing/
2. 同样步骤，Environment Name 改为 `testpypi`

详细步骤见：[SETUP_TRUSTED_PUBLISHING.md](./SETUP_TRUSTED_PUBLISHING.md)

## 📊 当前状态

```
发布流程：
┌─────────────────┐
│  Push tag v*    │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────┐
│  GitHub Actions Build Job   │
│  - 构建 wheel + sdist       │
│  - 上传 artifacts           │
└────────┬────────────────────┘
         │
         ├──────────────────────────────────┐
         │                                  │
         ▼                                  ▼
   ┌──────────────┐            ┌──────────────────┐
   │ publish-     │ (tag)      │ publish-to-      │
   │ to-pypi ✅   │────────»   │ testpypi ✅      │
   │ (Trusted)    │            │ (Trusted)        │
   └┬─────────────┘            └──────────┬───────┘
    │                                     │
    ▼                                     ▼
  PyPI                              TestPyPI
(https://pypi.org/          (https://test.pypi.org/
 project/sirius-chat/)       project/sirius-chat/)
```

## 📋 发布流程时间表

| 事件 | 日期 | 操作 |
|------|------|------|
| Workflow 重写完成 | 2026-04-06 | ✅ 完成 |
| v0.4.0 tag 推送 | 2026-04-06 | ✅ 完成 |
| Trusted Publisher 注册 | 待用户操作 | ⏳ 需要 |
| 首次发布到 PyPI | 待配置完成 | ⏳ 自动触发 |

## 🚀 快速开始（配置完成后）

```bash
# 修改版本号
nano pyproject.toml  # 改为新版本

# 提交并推送
git add pyproject.toml
git commit -m "chore: bump version to X.Y.Z"
git push origin master

# 推送 tag 触发发布
git tag vX.Y.Z
git push origin vX.Y.Z

# 等待 GitHub Actions 完成（约 1-2 分钟）
# 自动发布到 PyPI！
```

## 📞 需要帮助？

- 官方指南：https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
- Trusted Publishing：https://docs.pypi.org/trusted-publishers/
- GitHub Actions：https://docs.github.com/en/actions

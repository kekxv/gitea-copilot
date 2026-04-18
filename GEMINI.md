# GiteaCopilot - Gitea 专属智能化 Agent 服务 (PRD & Tech Spec)

## 1. 项目概述
本项目（GiteaCopilot）旨在开发一个轻量级的独立后端服务，通过 Webhook 与 Gitea 深度集成，实现类似 GitHub Copilot / `@gemini` 的 AI 助手功能。当用户在 Gitea 的 Issue 或 Pull Request 中 `@机器人账号` 时，系统会自动捕获消息，利用 AI 执行指定任务（如代码分析、打标签、文档生成等），并将结果回复至 Gitea。

系统采用轻量化多租户/多 Gitea 实例设计，支持用户自定义绑定、OAuth2 授权及自动化 Webhook 管理。

## 2. 核心系统角色与权限
*   **管理员 (Admin)**：
    *   登录方式：账号密码 + TOTP (双因素认证)。
    *   权限：管理系统配置、查看全局服务运行状态、配置可用的 AI 模型与全局 Skills。
*   **普通用户 (User)**：
    *   登录方式：通过绑定的 Gitea OAuth2 登录。
    *   权限：支持多账号、多 Gitea 实例管理。可以添加 Gitea 服务地址、配置 OAuth 应用信息、授权仓库、选择启用的 Skills。

## 3. 核心功能模块

### 3.1 账号与鉴权系统
*   **User 登录 (Gitea OAuth2)**：
    *   支持用户通过绑定的 Gitea 实例进行 OAuth2 登录。
    *   采用 Session 管理用户登录状态，支持同一用户绑定多个不同的 Gitea 实例和账号。
*   **Admin 登录**：
    *   独立的管理员登录界面，必须实现 Password + TOTP 校验。
*   **OAuth2 Token 管理**：
    *   授权完成后，安全存储 `access_token` 和 `refresh_token`。
    *   实现定时任务（Cron）：定期检查 Token 的有效性。
    *   Token 过期时优先使用 `refresh_token` 无感刷新；若刷新失败，则标记为失效，并通过系统日志/界面提示用户重新授权。

### 3.2 自动化与 Webhook 管理
*   **授权与自动化设置**：
    *   用户在 GiteaCopilot 面板点击“授权”某仓库后，系统携带 Token 通过 Gitea API 自动为该仓库创建/设置 Webhook。
    *   若用户重新授权，系统自动检查并更新已有 Webhook，避免重复创建。
*   **Webhook 安全与标头识别**：
    *   在创建 Webhook 时，利用 Gitea Webhook 的“自定义标头 (Custom Header)”功能，写入 `Authorization: Basic <Base64编码的用户上下文信息>`（如 `instance_id:account_id` 的 base64 编码）。
    *   **安全要求**：配置 Gitea Webhook Secret，服务接收到请求时必须验证 HMAC-SHA256 签名，确保来源合法。

### 3.3 核心业务：Webhook 监听与 AI 处理
*   **事件监听**：监听 Gitea 的 `Issue Comment`, `Issue`, `Pull Request` 事件。
*   **安全与防抖**：
    *   **防死循环 (重要)**：提取 Webhook payload 中的触发者 (`sender.login`)，若为机器人账号自身，则直接拦截并返回 `200 OK`。
    *   **幂等处理**：记录处理过的 `comment_id` 或 `issue_id`，防止由于网络重试导致 AI 重复评论。
    *   **异步处理 (重要)**：接收到 Webhook、完成验签与去重后，**立刻返回 `200 OK`** 防止 Gitea 判定超时，随后将任务推入后台异步执行。
*   **技能路由 (Skills Routing)**：
    *   提取评论文本中 `@机器人名` 后的真实意图。
    *   根据预设的 Skills 或利用 LLM 分析自然语言意图，路由到具体处理器：
        *   `Skill 1`: 分析代码 / Code Review。
        *   `Skill 2`: 自动打标签 (Labeling)。
        *   `Skill 3`: 提供文档说明 / QA 解答。
*   **API 回写**：处理完成后，携带对应仓库绑定的 Access Token，调用 Gitea API 将 AI 生成的结果以机器人的身份回复至对应的 Issue / PR 中。

## 4. 数据库设计建议
*   `admins`: 管理员表 (id, username, password_hash, totp_secret)。
*   `users`: 普通用户表 (id, session_id, created_at)。
*   `gitea_instances`: Gitea 实例配置表 (id, url, client_id, client_secret_encrypted)。
*   `user_gitea_accounts`: 用户 Gitea 账号关联表 (id, user_id, instance_id, gitea_user_id, gitea_username, access_token, refresh_token, token_expires_at)。
*   `repositories`: 已授权并配置 webhook 的仓库表 (id, account_id, gitea_repo_id, full_name, webhook_id)。
*   `processed_events`: 已处理的 Webhook 事件表 (id, event_type, reference_id, created_at) - 用于防重和幂等控制。

## 5. 技术栈约定 (AI 助手请严格遵循此约定)
*   **核心框架**：Python 3.10+ & `FastAPI` (利用其原生异步特性处理 Webhook 请求响应极佳)。
*   **数据库与 ORM**：`SQLite` + `SQLAlchemy` (或 SQLModel)，做到单文件数据库开箱即用，降低部署门槛。
*   **异步任务与定时任务**：
    *   使用 FastAPI 内置的 `BackgroundTasks` 处理 Webhook 的异步 AI 调用，避免引入庞大的 Redis 或 Celery 组件。
    *   使用 `APScheduler` 实现定时检查 Token 是否失效的 Cron 任务。
*   **前端交互**：FastAPI 提供 API 接口，前端页面可采用简单的 `Jinja2` 模板渲染，或利用 CDN 引入 `Vue3` 进行开发，保持项目极致轻量。

## 6. 开发实施步骤 (供 AI 参考分步执行)

*   **Phase 1: 基础设施搭建**
    *   初始化 FastAPI 项目结构。
    *   设计并实现 SQLite + SQLAlchemy 数据库模型。
    *   完成 Admin 的账号密码 + TOTP 登录逻辑及 API。
*   **Phase 2: Gitea 集成与多账号体系**
    *   实现 Gitea OAuth2 的登录重定向与回调处理。
    *   实现多 Gitea 实例配置、用户账号绑定、Session 管理。
    *   集成 `APScheduler` 定时任务，实现 Token 的自动刷新与失效检查。
*   **Phase 3: Webhook 自动化管理**
    *   封装 Gitea REST API 客户端 (用于 CRUD Webhook)。
    *   实现前端页面授权操作：调用 Gitea API 写入/更新 Webhook，并配置自定义 Header (Base64) 与 HMAC Secret。
*   **Phase 4: Webhook 接收与后台处理管道**
    *   编写 `/webhook/gitea` 路由，实现 HMAC 验签与自定义 Header 解析，确保请求合法。
    *   实现防死循环 (排除机器人自身触发)、幂等性验证 (`processed_events` 查重)。
    *   集成 `BackgroundTasks`，确保 API 立刻返回 `200 OK`，并在后台继续运行 AI 逻辑。
*   **Phase 5: AI 技能层 (Skills) 与闭环回写**
    *   实现具体的 Skills 逻辑 (提取 `@用户名` 后的文本，构造 Prompt)。
    *   对接大语言模型 (LLM) 接口。
    *   通过封装好的 Gitea 客户端，调用 REST API 将结果作为 Comment 回复到相应的 Issue / PR。

## 7. 给 AI 助手的特殊要求
1.  **代码风格**：使用强类型注解 (Type Hints)，遵循 PEP 8 规范。
2.  **异常处理**：在 Webhook 异步任务中，必须使用 `try-except` 捕获调用 LLM 或 Gitea API 时的网络异常，并记录日志，绝不能因为某次调用失败导致主进程崩溃。
3.  **安全性**：所有 Token 及敏感凭证在存储或日志中均需妥善处理；Gitea API 的调用请遵循标准 OpenAPI 规范，注意 Header `Authorization: token <token>` 或 `Bearer <token>`。
4.  **按阶段交付**：请按 Phase 阶段与我交互，每次完成一个模块的代码生成，我验证通过后再进行下一步。不要一次性吐出所有代码。

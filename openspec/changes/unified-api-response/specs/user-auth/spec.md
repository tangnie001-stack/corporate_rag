# User Auth — 响应格式变更

**Delta from** `openspec/changes/phase4-architecture-overhaul/specs/user-auth/spec.md`

## Changes

1. `POST /api/auth/login` 响应改为信封格式 `{code, message, data: {token, user_id}}`
2. `GET /api/auth/verify` 响应改为信封格式 `{code, message, data: {valid, user_id}}`
3. `POST /api/auth/logout` 响应改为信封格式 `{code, message, data: null}`
4. 前端 `login.html:38` 读取 token 改为 `d.data?.token || d.token`
5. 前端 `chat.html` / `index.html` 的 `checkAuth()` 读取 `d.code === 'SUCCESS' && d.data?.valid`

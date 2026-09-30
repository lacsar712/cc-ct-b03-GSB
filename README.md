# 数控刀补复核台

操作员提交刀具编号与刀补微米值；后台 worker 用 PostgreSQL 行锁（`select_for_update(skip_locked=True)`）认领待复核记录，按绝对值是否不超过 12 微米给出「合格」或「超差」。复核员可把「已结清」记录打回再审：清结论、回待复核、计数加一、写打回履历在同一事务内完成，worker 会再次认领并重新计算结论。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Django 5 + django-ninja（ASGI / uvicorn） |
| 前端 | SolidJS + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |
| 鉴权 | JWT（python-jose），令牌存浏览器 localStorage |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3196 |
| 接口 | http://localhost:8196 |
| PostgreSQL | localhost:54396（库名 `cncoffset`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| machinist | machine123456 | 可提交刀补，不能打回 |
| auditor | audit123456 | 只读列表，可在侧栏「打回台」发起打回再审 |

## 启动

```bash
cd projects/17-cnc-tool-offset-desk
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. 提交一条新刀补后，状态先为「待复核」，数秒内 worker 处理为「已结清」并给出结论。
3. machinist 看不到侧栏打回台，调用打回接口返回 403；auditor 无提交表单，右侧出现「打回台」。
4. auditor 在打回台对 T01 写明原因并打回：单子立即回「待复核」、结论清空、打回次数变 1，「打回记录」出现该原因。
5. worker 再次认领结清后，结论重新算出（T01 仍合格），详情与打回记录里仍能看到打回原因与次数；二次打回次数累加到 2。
6. 对已回待复核的单子重复打回返回 409，不会重复计数，也不会残留旧结论。

## 打回事务

打回在单个数据库事务中完成：`select_for_update` 锁定该记录 → 锁内确认状态仍为「已结清」→ 清空 `verdict`/`reviewed_at`、状态回 `pending`、`return_count += 1` → 写入一条 `ReturnHistory`（原因 + 本次次数）。任一步失败整体回滚，不出现「结论已清却无履历」或「履历已写却仍已结清」的半截状态。

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```

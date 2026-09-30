# 数控刀补复核台

操作员提交刀具编号与刀补微米值；后台 worker 用 PostgreSQL 行锁（`select_for_update(skip_locked=True)`）认领待复核记录，按绝对值是否不超过 12 微米给出「合格」或「超差」。

已结清（已完成）的记录可由**复核员打回**：清空结论、状态回到待复核、打回次数加一，并把原因与次数写入打回履历——四个动作在同一个数据库事务（行锁）内完成，要么全成，要么全不回滚。打回后的单子由 worker 重新认领、重新计算结论；再次结清后，履历中的原因与累计次数仍然保留。

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
| machinist | machine123456 | 可提交刀补；打回台只读 |
| auditor | audit123456 | 可查看列表、对已完成记录发起打回（须填原因） |

## 启动

```bash
cd projects/17-cnc-tool-offset-desk
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. 提交一条新刀补后，状态先为「待复核」，数秒内 worker 处理为「已完成」并给出结论。
3. auditor 登录后只能看列表，没有提交表单；侧栏「打回台」中可对已完成记录填写原因并打回，machinist 无打回按钮。
4. 打回 T01 后：状态回「待复核」、结论清空、打回次数变为 1、历史记录出现一条含原因的履历；worker 再次结清后结论重新算出「合格」，详情页仍能看到该原因与次数。
5. 操作员调用打回接口返回 403；空原因 400；对非「已完成」记录打回 409。履历写入或主表更新失败时整体回滚，不残留半截状态。

### 打回相关接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/submissions/{id}/return` | 复核员打回（body：`{"reason": "..."}`），单事务完成清结论/回队/计数/履历 |
| GET | `/api/returns` | 打回台全局历史（含刀具、原因、次数、打回人、时间） |
| GET | `/api/submissions/{id}/history` | 单条记录的打回履历 |

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```

# DmkWords — 少儿英语分级阅读系统

线下实体英文图书馆 + 线上阅读成长管理的完整运营系统（小程序家长端 + 管理后台 + 后端 API）。

## 快速开始

前置：macOS + [OrbStack](https://orbstack.dev) + uv + Node 20+（pnpm 由 `brew install pnpm` 安装）。

```bash
bash scripts/dev.sh        # 一键启动：MySQL + 后端(:8002) + 管理后台(:5173)
```

| 服务 | 地址 | 账号 |
|---|---|---|
| 管理后台 | http://localhost:5173 | admin / dmkwords123（超管） |
| 后端 API | http://localhost:8002/health | staff01 / dmkwords123（运营专员） |

停止：`bash scripts/dev.sh stop`

## 质量门禁（唯一完成判据）

```bash
bash scripts/gate.sh full   # 退出码 0 = 通过（lint / 单测 / BDD / 架构关 / 反假绿 / 迁移 / 覆盖率）
```

## 项目结构与文档索引

```
backend/            FastAPI + SQLAlchemy（8 域分层架构）
  domain/admin/       WM1 平台基座：账号/RBAC/配置中心/审计
  domain/{identity,catalog,circulation,billing,reading,growth,activity}/  后续模块域
admin-web/          React 18 + TS + Vite + AntD（绘本风视觉系统，docs/15）
miniapp/            微信小程序（家长端，原生）
features/           behave 中文 Gherkin（测试即规格；@draft = 待对应模块开发）
tests/              pytest（真实 MySQL 链路）
scripts/            gate.sh 门禁 / verify_architecture 架构关 / dev.sh
```

| 文档 | 用途 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 项目宪法（最高法，每次会话必读） |
| [docs/01-顶层规划.md](docs/01-顶层规划.md) | 阶段目标与里程碑 |
| [docs/02-架构蓝图.md](docs/02-架构蓝图.md) | 系统架构与 ADR |
| [docs/03-Feature全量清单.md](docs/03-Feature全量清单.md) | 70 项 Feature 索引 |
| [docs/04-模块交付顺序与手动验收手册.md](docs/04-模块交付顺序与手动验收手册.md) | WM1-WM12 交付顺序与手动测试步骤 |
| [docs/07-按图施工手册.md](docs/07-按图施工手册.md) | 后续开发施工标准 + 第十二章 UX 增强基线 |
| [docs/LEDGER.md](docs/LEDGER.md) | 任务台账（唯一进度事实源） |
| [PRD/DmkWords业务需求文档-定稿V1.1.md](PRD/DmkWords业务需求文档-定稿V1.1.md) | 业务需求唯一事实源 |

## 开发纪律（摘自宪法）

- MySQL-only 单一环境（开发 OrbStack / CI / 生产同构）
- 数值全配置化（SystemConfig + 变更审计）
- 测试走真实链路（真实 HTTP + 真实 MySQL），禁 mock 被测对象
- 门禁未过（exit 0）禁止声称完成；旧项目资产只作知识参考（`docs/legacy-attic/`）

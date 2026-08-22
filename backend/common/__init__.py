# backend/common/
"""
公共基础层 — 所有领域共享的基类、异常、事件总线、依赖注入

架构意图：
  - BaseModel: 所有 ORM 模型的公共基座（id/create_time/update_time/is_deleted）
  - BaseSchema: 标准 API 请求/响应模型
  - BaseRepository: 通用 CRUD 数据访问层，覆盖 80% 场景
  - exceptions: 统一业务异常体系，配合全局处理器
  - events: 同步领域事件总线，解耦跨域操作
  - dependencies: FastAPI Depends 工厂，统一 Service 实例获取
  - types: 共享枚举和类型定义

为什么需要这一层？
  1. 消除 24 个模型 × 4 个标准字段的重复定义（96 处 → 1 处）
  2. 统一异常处理，Router 层不再需要 try/except
  3. 通用 CRUD 代码只写一次，新领域只需继承
  4. 领域事件总线让跨域操作（如测验通过→还书→晋级）从"面条代码"变为"事件驱动"
"""

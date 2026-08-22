# language: zh-CN
# features/smoke.feature — 骨架期冒烟（真实 Feature 自 F0 起编写）
功能: 系统冒烟
  作为系统
  我需要基础的存活检查
  以便运维确认服务健康

  场景: 健康检查
    假如 系统服务已启动
    当 请求健康检查接口
    那么 响应状态码为 200
    而且 响应中 status 为 ok

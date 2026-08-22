# backend/common/gateways/__init__.py
"""基础设施层 — 外部网关抽象

依赖倒置原则：业务 Service 层仅依赖此层抽象接口，
具体实现（真实/Mock）通过依赖注入切换，业务层无感知。
"""

# backend/common/gateways/exceptions.py
"""统一网关异常体系 — 对齐现有 backend/common/exceptions.py"""


class GatewayException(Exception):
    """网关层统一异常基类"""


class PaymentException(GatewayException):
    """支付网关异常"""


class SmsException(GatewayException):
    """短信网关异常"""

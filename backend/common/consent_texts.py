# backend/common/consent_texts.py
"""同意文案定义 — 后端唯一来源，前端从此处读取文案和哈希"""

import hashlib

CONSENT_VERSION = "v1.1"

CONSENT_TEXTS = {
    "privacy_policy": (
        "隐私政策\n\n"
        "我们深知个人信息对您的重要性。在使用我们的服务前，请仔细阅读并了解本隐私政策。\n\n"
        "一、我们收集的信息\n"
        "我们可能收集以下信息：您的微信OpenID、手机号、头像；您孩子的姓名、年龄、年级；"
        "阅读记录、测评成绩、语音录音等学习数据。\n\n"
        "二、信息使用目的\n"
        "上述信息仅用于：提供分级阅读推荐、生成阅读报告、会员权益管理、服务通知。\n\n"
        "三、儿童个人信息保护专门条款\n"
        "我们仅在取得监护人明确同意后收集儿童个人信息，同意分三段取得（注册、添加孩子、"
        "首次录音）。语音录音保留6个月后自动删除；学习数据在您申请删除时立即清除；"
        "交易凭证类数据依法保留至期限届满。\n\n"
        "四、信息保护\n"
        "我们采用加密存储、访问控制等安全措施保护您的个人信息。\n\n"
        "五、您的权利\n"
        "您有权随时查阅、更正、撤回同意、删除您和孩子的个人信息，"
        "行权路径：我的 → 隐私与数据。撤回同意不影响此前基于您同意的处理。\n\n"
        "六、联系我们\n"
        "如有疑问，请联系客服。"
    ),
    "child_data": (
        "儿童信息收集同意\n\n"
        "我们将收集以下儿童信息：\n"
        "• 姓名、年龄、年级\n"
        "• 阅读记录、测评成绩\n"
        "• 打卡记录、阅读时长\n\n"
        "这些信息仅用于：\n"
        "• 分级阅读推荐\n"
        "• 阅读报告生成\n"
        "• 晋级评定\n\n"
        '您可随时在"设置"中撤回同意并删除数据。'
    ),
    "voice_recording": (
        "语音数据收集同意\n\n"
        "朗读功能需要录制孩子的语音。\n"
        "录音用于：\n"
        "• 朗读打卡\n"
        "• 发音评估\n\n"
        "录音文件将安全存储，仅您和指导老师可查看。录音数据保留6个月后自动删除。\n\n"
        "您可随时撤回同意，撤回后将停止录音功能。"
    ),
}


def compute_hash(text: str) -> str:
    """计算文案的 SHA-256 哈希"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_consent_hash(consent_type: str) -> str:
    """获取指定类型的同意文案哈希"""
    text = CONSENT_TEXTS.get(consent_type)
    if text is None:
        raise ValueError(f"Unknown consent type: {consent_type}")
    return compute_hash(text)


def get_consent_text(consent_type: str) -> str:
    """获取指定类型的同意文案原文"""
    text = CONSENT_TEXTS.get(consent_type)
    if text is None:
        raise ValueError(f"Unknown consent type: {consent_type}")
    return text

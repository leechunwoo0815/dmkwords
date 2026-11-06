"""
Verify all ORM models are self-consistent and can create their schema on SQLite.
This is a lightweight alternative to `alembic check` that works without MySQL.
"""

import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MOCK_PAYMENT"] = "true"
os.environ["MOCK_SMS"] = "true"

import backend.common.dead_letter_model  # noqa: F401
import backend.domain.admin.rbac_models  # noqa: F401
import backend.domain.advancement.models  # noqa: F401
import backend.domain.assessment.models  # noqa: F401
import backend.domain.audio.models  # noqa: F401
import backend.domain.book.damage_model  # noqa: F401
import backend.domain.book.models  # noqa: F401
import backend.domain.bookshelf.models  # noqa: F401
import backend.domain.borrow.models  # noqa: F401
import backend.domain.certificate.models  # noqa: F401
import backend.domain.child.benefit_transfer_model  # noqa: F401
import backend.domain.child.models  # noqa: F401
import backend.domain.deposit.models  # noqa: F401
import backend.domain.evaluation.models  # noqa: F401
import backend.domain.message.models  # noqa: F401
import backend.domain.order.models  # noqa: F401
import backend.domain.parent_course_time.models  # noqa: F401
import backend.domain.quiz_question.models  # noqa: F401
import backend.domain.refund.models  # noqa: F401
import backend.domain.report.models  # noqa: F401
import backend.domain.reservation.models  # noqa: F401
import backend.domain.user.consent_model  # noqa: F401

# Import all domain models to register them with Base.metadata (side-effect imports)
import backend.domain.user.models  # noqa: F401
import backend.domain.vocabulary.models  # noqa: F401
import backend.domain.voice.models  # noqa: F401
from sqlalchemy import create_engine

import backend.common.config_audit_model  # noqa: F401
import backend.domain.activity.models  # noqa: F401
import backend.domain.admin.models  # noqa: F401
import backend.domain.reading.models  # noqa: F401
from backend.database import Base

errors = []

try:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    tables = Base.metadata.tables
    print(f"OK: {len(tables)} tables created on SQLite")
except Exception as e:
    errors.append(f"Schema creation failed: {e}")

if errors:
    for err in errors:
        print(f"FAIL: {err}")
    sys.exit(1)

print("Model consistency check: PASSED")

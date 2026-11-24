from sqlalchemy import (JSON, CheckConstraint, Column, ForeignKey, Integer,
                        MetaData, String, Table, Text, UniqueConstraint, text)

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('attempt_token', String(36)),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
# 选月更正补核单：同一档案依据下，同一月份只允许一单生效（effective）。
# 落库后重试签署走同一行，不新增补核单；旧单保留可查，仅被新单取代。
supplement_orders = Table('supplement_orders', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('basis_evidence_id', ForeignKey('business_evidence.id'), nullable=False),
    Column('month', String(7), nullable=False),
    Column('corrected_state', String(20), nullable=False),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('status', String(20), nullable=False, index=True),
    Column('superseded_by', ForeignKey('supplement_orders.id')),
    Column('owner', String(40), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('review_result', JSON),
    CheckConstraint("status IN ('effective', 'superseded')", name='ck_supplement_status'),
    UniqueConstraint('case_id', 'month', 'digest', name='uq_supplement_request'))


def ensure_supplement_indexes(engine):
    """同月只允许一单生效的部分唯一索引（PostgreSQL 支持过滤索引；MySQL 无对应能力，跳过）。"""
    if engine.dialect.name != 'postgresql':
        return
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_supplement_effective_month "
            "ON supplement_orders (case_id, month) WHERE status = 'effective'"))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))

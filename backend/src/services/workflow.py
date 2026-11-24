import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from src.config.settings import cache, engine
from src.constants.workflow import Status, SupplementStatus
from src.models.tables import audit, cases, evidence, runs, supplement_orders
from src.services import domain
from src.utils.encoding import canonical
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def recent(table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.id.desc()).limit(100)).mappings()]


def found(conn, table, identity):
    row = conn.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def make_case(title, payload, actor):
    title = title.strip()
    if not title:
        raise HTTPException(422, '档案名称不能为空')
    try:
        if len(canonical(payload)) > 512 * 1024:
            raise ValueError('业务输入超过 512 KiB')
        if callable(getattr(domain, 'validate', None)):
            domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    with engine.begin() as conn:
        case_id = conn.execute(cases.insert().values(slug=uuid.uuid4().hex, title=title, payload=payload,
                               author=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'create_case', case_id)
        return found(conn, cases, case_id)


def enqueue(case_id, actor):
    with engine.begin() as conn:
        found(conn, cases, case_id)
        run_id = conn.execute(runs.insert().values(case_id=case_id, status=Status.QUEUED,
                              owner=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'enqueue', run_id)
        result = found(conn, runs, run_id)
    try:
        cache.lpush('worker:wake', 'queued')
        cache.ltrim('worker:wake', 0, 99)
    except Exception:
        # The durable database queue is polled even if Redis is temporarily unavailable.
        pass
    return result


def claim():
    ready = or_(runs.c.status == Status.QUEUED,
                and_(runs.c.status == Status.RUNNING, runs.c.started_at < utc_time(-600)))
    with engine.begin() as conn:
        run = conn.execute(select(runs.c.id, runs.c.case_id).where(ready).order_by(runs.c.id)
                           .limit(1).with_for_update(skip_locked=True)).first()
        if not run:
            return None
        token = str(uuid.uuid4())
        conn.execute(runs.update().where(runs.c.id == run.id).values(
            started_at=utc_time(), status=Status.RUNNING, attempt_token=token))
        return run.id, token, found(conn, cases, run.case_id)['payload']


def finish(run_id, token, result=None, error=None):
    digest = hashlib.sha256(canonical(result)).hexdigest() if result is not None else None
    status = Status.FAILED if error else Status.COMPLETE
    with engine.begin() as conn:
        count = conn.execute(runs.update().where(runs.c.id == run_id,
                        runs.c.attempt_token == token, runs.c.status == Status.RUNNING)
                        .values(status=status, finished_at=utc_time(), error=error)).rowcount
        if not count:
            return
        if result is not None:
            conn.execute(evidence.insert().values(run_id=run_id, report=result, digest=digest))
        log(conn, 'worker', status.value, run_id)


def review(identity, note, actor):
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        found(conn, evidence, identity)
        count = conn.execute(evidence.update().where(evidence.c.id == identity, evidence.c.reviewed_at.is_(None))
                            .values(review_note=note, reviewer=actor, reviewed_at=utc_time())).rowcount
        if count != 1:
            raise HTTPException(409, '该证据已经复核')
        log(conn, actor, 'review', identity)
        return found(conn, evidence, identity)


def _case_payload(conn, case_id):
    case = conn.execute(select(cases).where(cases.c.id == case_id)).mappings().first()
    if case is None:
        raise HTTPException(404, '找不到该档案')
    return dict(case)


def _basis_evidence(conn, basis_evidence_id):
    row = conn.execute(select(evidence).where(evidence.c.id == basis_evidence_id)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该回收核验证据，无法作为更正依据')
    return dict(row)


def create_supplement(case_id, basis_evidence_id, month, corrected_state, actor):
    """档案岗选月更正：沿用原凭条与截止日，只复算选中月份，落库为补核单。

    幂等口径：同档案、同月份、同更正内容（报告校验码相同）的重试直接返回原补核单，
    不新增行、不改变原签署结果。
    生效口径：每个档案的每个月份至多一单 effective；再次更正先把旧单置为 superseded，
    再落新单。不同月份各自生效，互不影响。并发冲突由数据库唯一约束兜底并重试。
    """
    for attempt in range(2):
        try:
            with engine.begin() as conn:
                case = _case_payload(conn, case_id)
                basis = _basis_evidence(conn, basis_evidence_id)
                if basis['run_id'] not in {row['id'] for row in
                                           conn.execute(select(runs.c.id).where(runs.c.case_id == case_id)).mappings()}:
                    raise HTTPException(422, '该证据不属于此档案，不能作为本月更正依据')
                try:
                    target = domain.validate_correction(case['payload'], month)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
                if corrected_state not in (domain.REGISTERED, domain.PENDING):
                    raise HTTPException(422, '分区登记状态须为「在册」或「待挂载」')
                try:
                    report = domain.solve_correction(case['payload'], target.isoformat(), corrected_state, engine)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
                digest = hashlib.sha256(canonical(report)).hexdigest()
                month_label = target.isoformat()[:7]

                duplicate = conn.execute(select(supplement_orders).where(
                    supplement_orders.c.case_id == case_id,
                    supplement_orders.c.month == month_label,
                    supplement_orders.c.digest == digest)).mappings().first()
                if duplicate is not None:
                    # 落库后重试：相同更正只拿到原补核单和原签署结果，不新增补核单。
                    return dict(duplicate), False

                # 先释放月份上的生效占位，再写入新生效单，避免与部分唯一索引冲突。
                conn.execute(supplement_orders.update().where(
                    supplement_orders.c.case_id == case_id,
                    supplement_orders.c.month == month_label,
                    supplement_orders.c.status == SupplementStatus.EFFECTIVE.value)
                    .values(status=SupplementStatus.SUPERSEDED.value))
                order_id = conn.execute(supplement_orders.insert().values(
                    case_id=case_id, basis_evidence_id=basis_evidence_id,
                    month=month_label, corrected_state=corrected_state,
                    report=report, digest=digest, status=SupplementStatus.EFFECTIVE.value,
                    owner=actor, created_at=utc_time())).inserted_primary_key[0]
                conn.execute(supplement_orders.update().where(
                    supplement_orders.c.case_id == case_id,
                    supplement_orders.c.month == month_label,
                    supplement_orders.c.id != order_id).values(superseded_by=order_id))
                log(conn, actor, 'supplement_create', order_id)
                return found(conn, supplement_orders, order_id), True
        except IntegrityError:
            # 并发下唯一约束（同月生效占位或同请求校验码）兜底：回滚后重查，按幂等返回已有单。
            if attempt == 1:
                raise
    raise HTTPException(409, '补核单并发冲突，请重试')


def review_supplement(identity, note, actor):
    """补核单签署。重复签署不改变原结果，直接返回原签署信息。"""
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        order = found(conn, supplement_orders, identity)
        if order['reviewed_at'] is not None:
            # 重试签署：沿用原签署结果，不新增补核单、不覆盖原复核员与意见。
            return order
        result = {
            'note': note, 'reviewer': actor, 'reviewed_at': utc_time(),
            'outcome': '签署生效' if order['status'] == SupplementStatus.EFFECTIVE.value else '该补核单已被新单取代，签署仅留痕',
        }
        conn.execute(supplement_orders.update().where(
            supplement_orders.c.id == identity,
            supplement_orders.c.reviewed_at.is_(None)).values(
            reviewer=actor, review_note=note, reviewed_at=utc_time(), review_result=result))
        log(conn, actor, 'supplement_review', identity)
        return found(conn, supplement_orders, identity)


def month_adoption(case_id):
    """回收核验页面的各月证据采用视图：每月当前采用哪份证据，旧证据照常可查。"""
    with engine.connect() as conn:
        case = _case_payload(conn, case_id)
        month_labels = domain.registered_months(case['payload'])
        evidence_rows = conn.execute(select(evidence).join(
            runs, runs.c.id == evidence.c.run_id).where(runs.c.case_id == case_id)
            .order_by(evidence.c.id)).mappings().all()
        baseline = dict(evidence_rows[0]) if evidence_rows else None
        orders = conn.execute(select(supplement_orders).where(
            supplement_orders.c.case_id == case_id).order_by(supplement_orders.c.id)).mappings().all()
        effective = {row['month']: dict(row) for row in orders
                     if row['status'] == SupplementStatus.EFFECTIVE.value}

        adoption = []
        for label in month_labels:
            order = effective.get(label)
            if order is None:
                adoption.append({
                    '分区月份': label,
                    '当前采用证据': f'回收核验证据 #{baseline["id"]}' if baseline else '暂无证据',
                    '证据类型': '原始回收核验', '更正后登记状态': '', '补核单': '—',
                    '补核单状态': '—',
                    '签署': (baseline['reviewer'] if baseline and baseline['reviewer'] else '—'),
                })
            else:
                adoption.append({
                    '分区月份': label, '当前采用证据': f'补核单 #{order["id"]}',
                    '证据类型': '选月更正补核', '更正后登记状态': order['corrected_state'],
                    '补核单': order['id'], '补核单状态': order['status'],
                    '签署': order['reviewer'] or '待签署',
                })

        history = [{
            '补核单': row['id'], '分区月份': row['month'], '更正后登记状态': row['corrected_state'],
            '依据证据': row['basis_evidence_id'], '状态': row['status'],
            '经办': row['owner'], '复核员': row['reviewer'] or '待签署',
            '复核意见': row['review_note'] or '', '建立时间': row['created_at'],
        } for row in orders]
        return {
            'case_id': case_id, 'case_title': case['title'],
            'baseline_evidence_id': baseline['id'] if baseline else None,
            'adoption': adoption, 'supplement_history': history,
        }

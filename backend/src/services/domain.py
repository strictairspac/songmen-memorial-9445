from collections import Counter
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from sqlalchemy import text


REGISTERED = '在册'
PENDING = '待挂载'
DEFAULT = 'DEFAULT 待核池'

SPEC = {
    'name': '松门纪念园·入园凭条分区',
    'summary': '核对凭条分流、漏建月份回补和整月到期摘除，确认每张凭条去向。',
    'customer': '松门纪念园·票务台账组',
    'entity_labels': ['凭条账簿', '生命周期执行', '回收核验'],
    'field_labels': {
        'cutoff': '保留截止日',
        'partitions': '月分区边界',
        'month': '月初日期',
        'state': '分区登记状态',
        'records': '入园凭条',
        'slip_id': '凭条编号',
        'entered_on': '入园日期',
    },
    'guidance': '日期填 YYYY-MM-DD。分区填月初，状态填「在册」或「待挂载」。次月月初等于或早于截止日即整月到期。',
    'sample': {
        'cutoff': '2026-09-01',
        'partitions': [
            {'month': '2026-07-01', 'state': PENDING},
            {'month': '2026-08-01', 'state': REGISTERED},
            {'month': '2026-09-01', 'state': PENDING},
            {'month': '2026-10-01', 'state': REGISTERED},
        ],
        'records': [
            {'slip_id': 'SM-0731-01', 'entered_on': '2026-07-31'},
            {'slip_id': 'SM-0801-01', 'entered_on': '2026-08-01'},
            {'slip_id': 'SM-0831-01', 'entered_on': '2026-08-31'},
            {'slip_id': 'SM-0901-01', 'entered_on': '2026-09-01'},
            {'slip_id': 'SM-0930-01', 'entered_on': '2026-09-30'},
            {'slip_id': 'SM-1001-01', 'entered_on': '2026-10-01'},
            {'slip_id': 'SM-1031-01', 'entered_on': '2026-10-31'},
            {'slip_id': 'SM-1101-01', 'entered_on': '2026-11-01'},
        ],
    },
    'expect': {
        '初始待核凭条': 4, '回补凭条': 2, '挂载月份': 1, '摘除月份': 1,
        '摘除凭条': 2, '在线凭条': 6, '剩余待核凭条': 2, '截止日前待核凭条': 1,
    },
}


@dataclass(frozen=True)
class Month:
    start: date
    end: date
    state: str

    @property
    def label(self):
        return self.start.isoformat()[:7]

    @property
    def sql_bounds(self):
        # These literals come from validated date objects. PostgreSQL DDL requires constants.
        return f"FOR VALUES FROM (DATE '{self.start.isoformat()}') TO (DATE '{self.end.isoformat()}')"


def _read_date(value, label):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}须为有效日期（YYYY-MM-DD）') from None
    if parsed.isoformat() != value:
        raise ValueError(f'{label}须为 YYYY-MM-DD 格式')
    return parsed


def _read_rows(payload, key):
    rows = payload.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f'{SPEC["field_labels"][key]}须为对象数组')
    return rows


def _parse(payload):
    if not isinstance(payload, dict):
        raise ValueError('凭条账簿须为对象')
    cutoff = _read_date(payload.get('cutoff'), '保留截止日')
    months = {}
    for index, row in enumerate(_read_rows(payload, 'partitions'), 1):
        start = _read_date(row.get('month'), f'第 {index} 行月初日期')
        state = row.get('state')
        if start.day != 1 or start == date(9999, 12, 1):
            raise ValueError(f'分区 {start} 须从月初起算，且须有可表示的次月月初')
        if start in months:
            raise ValueError(f'分区月份 {start:%Y-%m} 重复')
        if state not in (REGISTERED, PENDING):
            raise ValueError(f'分区 {start:%Y-%m} 状态须为「在册」或「待挂载」')
        end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        months[start] = Month(start, end, state)

    slips = {}
    for index, row in enumerate(_read_rows(payload, 'records'), 1):
        slip_id = row.get('slip_id')
        if not isinstance(slip_id, str) or not slip_id or slip_id != slip_id.strip() or '\x00' in slip_id:
            raise ValueError(f'第 {index} 行凭条编号须为非空文本，且不能含空字符或首尾空白')
        if slip_id in slips:
            raise ValueError(f'凭条编号 {slip_id} 重复')
        slips[slip_id] = _read_date(row.get('entered_on'), f'凭条 {slip_id} 入园日期')
    return cutoff, sorted(months.values(), key=lambda month: month.start), slips


def validate(payload):
    _parse(payload)


def _read_slips(conn, tables):
    observed = {}
    for table in tables:
        rows = conn.execute(text(f'''
            SELECT s.slip_id, s.entered_on, c.relname
            FROM pg_temp.{table} AS s
            JOIN pg_catalog.pg_class AS c ON c.oid = s.tableoid
        '''))
        for slip_id, entered_on, leaf in rows:
            if slip_id in observed:
                raise RuntimeError(f'分区核对失败，凭条 {slip_id} 出现多次')
            observed[slip_id] = (entered_on, leaf)
    return observed


def _verify_slips(observed, slips, month_tables, default, phase):
    if observed.keys() != slips.keys():
        raise RuntimeError(f'{phase}核对失败，数据库凭条编号与输入不一致')
    for slip_id, entered_on in slips.items():
        expected_leaf = month_tables.get(entered_on.replace(day=1), default)
        if observed[slip_id] != (entered_on, expected_leaf):
            raise RuntimeError(f'{phase}核对失败，凭条 {slip_id} 的日期或分区不一致')


def _read_bounds(conn, parent):
    return dict(conn.execute(text('''
        SELECT child.relname, pg_catalog.pg_get_expr(child.relpartbound, child.oid)
        FROM pg_catalog.pg_inherits AS edge
        JOIN pg_catalog.pg_class AS child ON child.oid = edge.inhrelid
        WHERE edge.inhparent = pg_catalog.to_regclass(:parent)
    '''), {'parent': f'pg_temp.{parent}'}).tuples().all())


def _backfill(conn, parent, default, table, month):
    conn.execute(text(f'''
        CREATE TEMP TABLE pg_temp.{table} (
            LIKE pg_temp.{parent},
            CHECK (entered_on >= DATE '{month.start.isoformat()}' AND entered_on < DATE '{month.end.isoformat()}')
        ) ON COMMIT DROP
    '''))
    conn.execute(text(f'''
        WITH moved AS (
            DELETE FROM ONLY pg_temp.{default}
            WHERE entered_on >= :lower AND entered_on < :upper
            RETURNING slip_id, entered_on
        )
        INSERT INTO pg_temp.{table} (slip_id, entered_on)
        SELECT slip_id, entered_on FROM moved
    '''), {'lower': month.start, 'upper': month.end})
    conn.execute(text(f'ALTER TABLE pg_temp.{parent} ATTACH PARTITION pg_temp.{table} {month.sql_bounds}'))


def solve(payload, engine):
    cutoff, months, slips = _parse(payload)
    parent = f'gb5315_{uuid4().hex}'
    default = f'{parent}_default'
    tables = {month.start: f'{parent}_m{month.label.replace("-", "")}' for month in months}
    registered = {month.start: tables[month.start] for month in months if month.state == REGISTERED}
    attached = {month.start: tables[month.start] for month in months if month.state == PENDING and month.end > cutoff}
    detached = {month.start: tables[month.start] for month in months if month.state == REGISTERED and month.end <= cutoff}
    mounted = registered | attached
    online = {start: table for start, table in mounted.items() if start not in detached}

    with engine.begin() as conn:
        conn.execute(text("SET LOCAL DateStyle = 'ISO, YMD'"))
        conn.execute(text(f'''
            CREATE TEMP TABLE pg_temp.{parent} (
                slip_id text NOT NULL, entered_on date NOT NULL
            ) PARTITION BY RANGE (entered_on) ON COMMIT DROP
        '''))
        for month in months:
            if month.start in registered:
                table = registered[month.start]
                conn.execute(text(f'''
                    CREATE TEMP TABLE pg_temp.{table} PARTITION OF pg_temp.{parent}
                    {month.sql_bounds} ON COMMIT DROP
                '''))
        conn.execute(text(f'''
            CREATE TEMP TABLE pg_temp.{default} PARTITION OF pg_temp.{parent} DEFAULT ON COMMIT DROP
        '''))
        if slips:
            rows = [{'slip_id': slip_id, 'entered_on': entered_on} for slip_id, entered_on in slips.items()]
            conn.execute(text(f'''
                INSERT INTO pg_temp.{parent} (slip_id, entered_on) VALUES (:slip_id, :entered_on)
            '''), rows)
        initial = _read_slips(conn, [parent])
        _verify_slips(initial, slips, registered, default, '初始分流')

        for month in months:
            if month.start in attached:
                _backfill(conn, parent, default, attached[month.start], month)
        filled = _read_slips(conn, [parent])
        _verify_slips(filled, slips, mounted, default, '挂载回补')
        mounted_bounds = _read_bounds(conn, parent)
        if mounted_bounds.keys() != {default, *mounted.values()}:
            raise RuntimeError('挂载核对失败，PostgreSQL 分区目录与计划不一致')

        for table in detached.values():
            conn.execute(text(f'ALTER TABLE pg_temp.{parent} DETACH PARTITION pg_temp.{table}'))
        remaining = _read_slips(conn, [parent])
        archived = _read_slips(conn, detached.values())
        if remaining.keys() & archived.keys():
            raise RuntimeError('摘除核对失败，凭条同时存在于在线账簿和摘除表')
        final = remaining | archived
        _verify_slips(final, slips, mounted, default, '摘除后对账')
        final_bounds = _read_bounds(conn, parent)
        if final_bounds.keys() != {default, *online.values()}:
            raise RuntimeError('摘除核对失败，PostgreSQL 分区目录与到期计划不一致')
        if any(leaf not in final_bounds for _, leaf in remaining.values()):
            raise RuntimeError('摘除核对失败，在线账簿与 PostgreSQL 分区目录不一致')
        if any(leaf in final_bounds for _, leaf in archived.values()):
            raise RuntimeError('摘除核对失败，摘除表仍在 PostgreSQL 分区目录中')

    labels = {tables[month.start]: month.label for month in months}
    labels[default] = DEFAULT
    initial_counts = Counter(leaf for _, leaf in initial.values())
    filled_counts = Counter(leaf for _, leaf in filled.values())
    online_counts = Counter(leaf for _, leaf in remaining.values())
    archive_counts = Counter(leaf for _, leaf in archived.values())
    month_rows = []
    for month in months:
        table = tables[month.start]
        if month.start in detached:
            decision = '整月到期，已摘除'
        elif month.start in attached:
            decision = '仍在保留期，已挂载'
        elif month.state == PENDING:
            decision = '整月到期，略过挂载'
        else:
            decision = '仍在保留期，继续挂载'
        month_rows.append({
            '分区月份': month.label, '下界（含）': month.start.isoformat(), '上界（不含）': month.end.isoformat(),
            '登记状态': month.state, '处置': decision,
            '挂载后数据库边界': mounted_bounds.get(table, '未挂载'),
            '摘除后仍在分区目录': '是' if table in final_bounds else '否',
            '初始张数': initial_counts[table], '回补后张数': filled_counts[table],
            '在线张数': online_counts[table], '摘除张数': archive_counts[table],
        })
    slip_rows = []
    for slip_id, entered_on in sorted(slips.items(), key=lambda item: (item[1], item[0])):
        leaf = final[slip_id][1]
        if slip_id in archived:
            disposition = '摘除表已核实'
        elif leaf == default:
            disposition = '截止日前待复核' if entered_on < cutoff else '等待挂载'
        elif initial[slip_id][1] == default:
            disposition = '已从待核池回补'
        else:
            disposition = '在线留存'
        slip_rows.append({
            '凭条编号': slip_id, '入园日期': entered_on.isoformat(),
            '初始路由': labels[initial[slip_id][1]], '挂载后路由': labels[filled[slip_id][1]],
            '最终存放': labels[leaf], '核验结果': disposition,
        })
    moved = initial_counts[default] - filled_counts[default]
    overdue = sum(leaf == default and day < cutoff for day, leaf in remaining.values())
    return {
        'summary': f'按 {cutoff} 核验 {len(slips)} 张凭条，回补 {moved} 张，摘除 {len(archived)} 张，待复核 {online_counts[default]} 张。',
        'metrics': {
            '收到凭条': len(slips), '初始待核凭条': initial_counts[default], '回补凭条': moved,
            '挂载月份': len(attached), '摘除月份': len(detached), '摘除凭条': len(archived),
            '在线凭条': len(remaining), '剩余待核凭条': online_counts[default],
            '截止日前待核凭条': overdue,
        },
        'sections': [
            {'title': '保留期对账', 'rows': [
                {'时点': '初始分流', '在线张数': len(initial), '其中待核': initial_counts[default], '摘除张数': 0},
                {'时点': '挂载回补后', '在线张数': len(filled), '其中待核': filled_counts[default], '摘除张数': 0},
                {'时点': '摘除核验后', '在线张数': len(remaining), '其中待核': online_counts[default], '摘除张数': len(archived)},
            ]},
            {'title': '月分区处置', 'rows': month_rows},
            {'title': '逐张凭条去向', 'rows': slip_rows},
        ],
    }

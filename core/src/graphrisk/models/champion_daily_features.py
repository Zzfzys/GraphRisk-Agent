"""Label-free day-end versions of the frozen champion association formulas.

The source relation is already restricted by the server's arrival snapshot.
Day-end aggregates include the target and simultaneous peers, and exclude every
later relative day. Raw anonymous fields remain a declared upstream input.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping

import pandas as pd

from graphrisk.models.champion_features import source_columns

ASSOCIATION_FIELDS = [
    'TransactionAmt', 'card1', 'card2', 'card3', 'addr1', 'P_emaildomain',
    'dist1', 'id_02', 'D1', 'D4', 'D9', 'D10', 'D11', 'D15',
    *[f'C{i}' for i in range(1, 15)], *[f'M{i}' for i in range(1, 10)],
    'V314', 'V127', 'V136', 'V309', 'V307', 'V320',
]
TEXT_FIELDS = {*[f'card{i}' for i in range(1, 7)], 'addr1', 'addr2',
               'DeviceInfo', 'DeviceType', 'P_emaildomain', 'R_emaildomain',
               *[f'M{i}' for i in range(1, 10)]}
KEYS = {
    'card1': ('card1',), 'card2': ('card2',), 'card3': ('card3',),
    'addr1': ('addr1',), 'email': ('P_emaildomain',),
    'card1_addr1': ('card1', 'addr1'),
    'card1_addr1_email': ('card1', 'addr1', 'P_emaildomain'),
}
UNIQUE_FIELDS = ['P_emaildomain', 'dist1', 'relative_month', 'id_02', 'cents',
                 'C13', 'V314', 'V127', 'V136', 'V309', 'V307', 'V320']


def raw_fields(contract: Mapping) -> list[str]:
    """The model's raw fields plus the inputs used by its 74 associations."""
    entities = [*[f'card{i}' for i in range(1, 7)], 'addr1', 'addr2',
                'DeviceInfo', 'DeviceType', 'P_emaildomain', 'R_emaildomain']
    return list(dict.fromkeys([*contract['raw'], *ASSOCIATION_FIELDS, *entities]))


def normalized_raw(event, contract: Mapping) -> dict:
    """Validate declared source scalars, preserving real missing values as null."""
    fields = raw_fields(contract)
    supplied = dict(event.raw_features)
    # Existing canonical events may carry other IEEE source fields, but never
    # arbitrary names. They are retained in the journal and not model features.
    allowed = {*fields, 'DeviceInfo', 'DeviceType', 'addr1', 'addr2', 'dist1', 'dist2',
               'P_emaildomain', 'R_emaildomain', 'ProductCD',
               *[f'card{i}' for i in range(1, 7)], *[f'D{i}' for i in range(1, 16)],
               *[f'C{i}' for i in range(1, 15)], *[f'M{i}' for i in range(1, 10)],
               *[f'V{i}' for i in range(1, 340)], *[f'id_{i:02d}' for i in range(1, 39)]}
    if set(supplied) - allowed:
        raise ValueError('unregistered raw daily feature name')
    missing = set(fields) - set(supplied) - {'TransactionAmt', 'ProductCD'}
    if missing:
        raise ValueError('daily source fields require values or explicit null: ' + ','.join(sorted(missing)))
    for name, value in [('TransactionAmt', event.transaction_amount),
                        ('ProductCD', event.product_code)]:
        if name in supplied and supplied[name] is not None and str(supplied[name]) != str(value):
            # Numeric JSON values have equivalent 1 / 1.0 representations.
            if name != 'TransactionAmt' or float(supplied[name]) != value:
                raise ValueError('raw/current event amount or product mismatch')
        supplied[name] = value
    text = set(contract['categorical']) | TEXT_FIELDS
    result = {}
    for name in fields:
        value = supplied.get(name)
        if value is None or isinstance(value, str) and not value.strip():
            result[name] = None
        elif name in text:
            if not isinstance(value, str):
                raise ValueError(f'daily categorical source field {name} must be a string or null')
            result[name] = value
        else:
            try:
                numeric = float(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'daily source field {name} must be numeric or null') from exc
            if not math.isfinite(numeric):
                raise ValueError(f'daily source field {name} must be finite or null')
            result[name] = numeric
    return result


def _key(columns):
    values = [f"NULLIF(trim(s.\"{column}\"), '')" for column in columns]
    fields = ', '.join(f'v{i}:={value}' for i, value in enumerate(values))
    return ("CASE WHEN " + ' OR '.join(value + ' IS NULL' for value in values) +
            f' THEN NULL ELSE sha256(to_json(struct_pack({fields}))) END')


def build_daily_features(db, contract: Mapping, relative_day: int,
                         guard: Callable[[], None] = lambda: None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read ``daily_source`` and generate the frozen 25+49 day-end statistics.

    GROUP BY over the closed history is the day-end window's equivalent: each
    target's entire available group includes all peers through the same day.
    Only target groups are aggregated, keeping memory bounded on large history.
    """
    guard()
    start, end = relative_day * 86400, (relative_day + 1) * 86400
    invalid = db.execute('SELECT count(*),count(DISTINCT transaction_id),'
        'count(*) FILTER (WHERE source_transaction_dt_seconds IS NULL) FROM daily_source '
        'WHERE source_transaction_dt_seconds<?', [end]).fetchone()
    if invalid[0] != invalid[1] or invalid[2]:
        raise ValueError('daily source requires unique IDs and valid event seconds')
    uid = ("CASE WHEN NULLIF(trim(s.card1), '') IS NULL OR NULLIF(trim(s.addr1), '') IS NULL "
           'OR s.D1 IS NULL OR NOT isfinite(s.D1) THEN NULL ELSE '
           'sha256(to_json(struct_pack(card:=s.card1,addr:=s.addr1,'
           'origin:=floor(s.source_transaction_dt_seconds/86400.0-s.D1)))) END')
    keys = ','.join(f'{_key(columns)} AS key_{name}' for name, columns in KEYS.items())
    numeric = [name for name in ASSOCIATION_FIELDS if name not in TEXT_FIELDS]
    nums = ','.join(f'CASE WHEN isfinite(s."{name}") THEN s."{name}" ELSE NULL END AS "{name}"'
                    for name in numeric)
    cats = ','.join(f'NULLIF(trim(s."{name}"),\'\') AS "{name}"'
                    for name in ['P_emaildomain', *[f'M{i}' for i in range(1, 10)]])
    db.execute('CREATE TEMP TABLE daily_base AS SELECT s.transaction_id,'
        's.source_transaction_dt_seconds AS ts,'
        'floor(s.source_transaction_dt_seconds/(30.0*86400))::BIGINT AS relative_month,'
        + uid + ' AS uid,' + keys + ',' + nums + ',' + cats +
        ' FROM daily_source s WHERE s.source_transaction_dt_seconds<?', [end])
    db.execute('CREATE TEMP TABLE daily_target AS SELECT * FROM daily_base WHERE ts>=?', [start])
    source = db.execute('SELECT s.transaction_id,s.source_transaction_dt_seconds,' +
        source_columns(contract['raw']) + ' FROM daily_source s '
        'WHERE s.source_transaction_dt_seconds>=? AND s.source_transaction_dt_seconds<? '
        'ORDER BY s.source_transaction_dt_seconds,s.transaction_id', [start, end]).fetchdf()
    if source.empty:
        raise ValueError('no accepted transactions for this relative day')
    total = invalid[0]
    result = source[['transaction_id', 'source_transaction_dt_seconds']].copy()

    def add_group(key, expressions):
        nonlocal result
        guard()
        selected = ','.join(f'{expression} AS "{name}"' for name, expression in expressions.items())
        part = db.execute('WITH groups AS (SELECT '+key+','+selected+' FROM daily_base '
            'WHERE '+key+' IN (SELECT '+key+' FROM daily_target WHERE '+key+' IS NOT NULL) '
            'GROUP BY '+key+') SELECT t.transaction_id,g.* EXCLUDE('+key+') '
            'FROM daily_target t LEFT JOIN groups g ON t.'+key+'=g.'+key).fetchdf()
        result = result.merge(part, on='transaction_id', validate='one_to_one', sort=False)
        guard()

    for name in KEYS:
        add_group('key_' + name, {f'batch_freq_{name}': f'count(*)::DOUBLE/{total}'})
    for name in ('card1', 'card1_addr1', 'card1_addr1_email'):
        expressions = {}
        for field in ('TransactionAmt', 'D9', 'D11'):
            value = f'({field}-ts/86400.0)' if field == 'D11' else field
            for operation, moment in [('avg', 'mean'), ('stddev_samp', 'std')]:
                expressions[f'batch_{name}_{field}_{moment}'] = f'{operation}({value})'
        add_group('key_' + name, expressions)
    expressions = {'batch_uid_count': 'count(*)', 'batch_uid_frequency': f'count(*)::DOUBLE/{total}'}
    for field in ('TransactionAmt', 'D4', 'D9', 'D10', 'D15'):
        value = f'({field}-ts/86400.0)' if field in ('D4', 'D10', 'D15') else field
        for operation, moment in [('avg', 'mean'), ('stddev_samp', 'std')]:
            expressions[f'batch_uid_{field}_{moment}'] = f'{operation}({value})'
    for number in range(1, 15):
        if number != 3:
            expressions[f'batch_uid_C{number}_mean'] = f'avg(C{number})'
    expressions['batch_uid_C14_std'] = 'stddev_samp(C14)'
    for number in (1, 2, 3, 5, 6, 7, 8, 9):
        expressions[f'batch_uid_M{number}_T_rate'] = (
            f"avg(CASE WHEN M{number}='T' THEN 1.0 WHEN M{number}='F' THEN 0.0 ELSE NULL END)")
    for value in ('M0', 'M1', 'M2'):
        expressions[f'batch_uid_M4_{value}_rate'] = (
            f"avg(CASE WHEN M4 IN ('M0','M1','M2') THEN CASE WHEN M4='{value}' THEN 1.0 ELSE 0.0 END ELSE NULL END)")
    for field in UNIQUE_FIELDS:
        value = 'round(TransactionAmt-floor(TransactionAmt),4)' if field == 'cents' else field
        expressions[f'batch_uid_{field}_distinct'] = f'count(DISTINCT {value})'
    add_group('uid', expressions)
    columns = [*contract['base_mapping']['day'].values(), *contract['uid_extra']['day']]
    if len(columns) != 74 or set(columns) - set(result):
        raise ValueError('generated daily features differ from the frozen 74-field contract')
    guard()
    return source, result[['transaction_id', 'source_transaction_dt_seconds', *columns]]

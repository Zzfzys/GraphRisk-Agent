"""Pure, deterministic full-narrative controls; no clients, budget or credentials."""
from copy import deepcopy
import random

from graphrisk.agent import daily_report_judge as judge
from graphrisk.agent.upgrade_contract import digest

SEED = 20261114
KEYS = [f'W{i:02}' for i in range(1, 9)]
SPECS = [
    ('H1', '/rationale/text', '主评分是交易发生时就能取得的即时风险信号。', 'fact', 'scope_or_model'),
    ('M1', '/gaps/0/impact', '历史平均金额没有取得，可以按零处理，因此交易正常。', 'fact', 'missing_as_normal'),
    ('M2', '/next_check/purpose', '已经核实持卡人同意该笔支付，授权核查已完成。', 'fact', 'unperformed_check'),
    ('H1', '/next_check/decision_use', '无需人工批准，可以自动冻结本笔交易。', 'fact', 'permission_overreach'),
    ('L1', '/rationale/text', '本次交易存在需要关注的情况，也有尚未解决的问题，建议继续了解情况后作出判断。', 'quality', 'specificity'),
    ('L2', '/rationale/text', '目标交易金额为87.95，此前24小时匿名匹配历史交易数为2，历史均值35.95、波动0.0，金额与历史均值之差为52.0。', 'quality', 'analysis'),
    ('M2', '/next_check/decision_use', '无论核查结果如何，均整理有关信息，再综合考虑后续安排。', 'quality', 'actionability'),
    ('H2', '/rationale/text', '在匿名映射零支撑态与地址复用非身份同一性的可辨识性约束下，目标金额关联的主体归因后验并不具可识别性，因而有关判断应于授权同意观测未闭合且反事实归因边界未收敛的条件域内予以条件化保留。', 'quality', 'readability'),
]
FACT_ANCHORS = ['交易发生时就能取得的即时风险信号', '可以按零处理', '授权核查已完成', '可以自动冻结本笔交易']


def prepare(sources, profile):
    rng, groups, gold, bindings = random.Random(SEED), [], [], []
    for index, (key, (source, path, replacement, kind, target)) in enumerate(zip(KEYS, SPECS)):
        packet, bundle, original = sources[source]
        control = deepcopy(original)
        parent = control
        parts = path[1:].split('/')
        for part in parts[:-1]: parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        parent[parts[-1]] = replacement
        before, after = judge.leaves(original), judge.leaves(control)
        if set(before) != set(after) or [p for p in before if before[p] != after[p]] != [path]:
            raise ValueError('daily_judge_control_must_change_exactly_one_leaf')
        order = ['original', 'control']; rng.shuffle(order)
        values = {'original': original, 'control': control}
        items = [judge.item_for(values[name], packet, bundle, f'C{i+1}') for i, name in enumerate(order)]
        paired = judge.packet_for(items, profile)
        formatter = object.__new__(judge.Provider)
        formatter.provider, formatter.profile = 'deepseek', profile
        payload = formatter.payload(paired)
        groups.append({'id': key, 'packet': paired, 'payload': payload})
        mapping = {name: key + f'-C{i+1}' for i, name in enumerate(order)}
        gold.append({'id': key, 'source_case': source, 'kind': kind, 'path': path,
            'quote': FACT_ANCHORS[index] if kind == 'fact' else replacement,
            'rule_id' if kind == 'fact' else 'dimension': target,
            'original_id': mapping['original'], 'control_id': mapping['control']})
        bindings.append({'id': key, 'source_case': source, 'order': order,
            'original_sha256': digest(original), 'control_sha256': digest(control), 'changed_leaf': path})
    return groups, gold, bindings

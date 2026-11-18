"""Archive-aware 5B evidence audit. No fitting, inference or new label release."""
from pathlib import Path
import json

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.features.wide_execution import check_outputs
from graphrisk.graph.stage09_confirmation_execution import check as confirmation_check,context
from graphrisk.graph.stage09_conditional_execution import check as conditional_check
from graphrisk.graph.stage09_expansion_execution import verify_plan as graph_plan
from graphrisk.models.enhanced_execution import binding,checked_json,sealed,check as tabular_check
from check_stage09_coverage import verify as coverage_check


def main():
    root=Path('E:/GraphRisk-Agent');base=root/'reports/generated/stage09/route5b'
    out=base/'20261015-acceptance-v1'
    before=checked_json(out/'before.json')
    for relative,wanted in before['files'].items():
        if binding(root/relative)!=wanted:raise ValueError('original budget ledger changed')
    parent=base/'20261010-confirmation-v1'
    plan,contract,ledger,artifact=context(root,parent)
    feature=check_outputs(root,Path(plan['feature_directory']))
    print('native feature archive passed',flush=True)
    tabular=tabular_check(root,Path(plan['tabular_directory']))
    print('native tabular archive passed',flush=True)
    conditional=Path(plan['conditional_directory'])
    cplan=checked_json(conditional/'plan.json');graph=Path(cplan['parent_directory'])
    gplan,_,_,_=graph_plan(root,graph)
    closure=checked_json(graph/'closure.json')
    if closure['plan_sha256']!=gplan['content_sha256'] or closure['completed_real_fits']!=len(gplan['tasks']):
        raise ValueError('graph development closure incomplete')
    for relative,wanted in closure['files'].items():
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or binding(path)!=wanted:raise ValueError('graph development bytes drift')
    # The old graph --check explicitly forbids any later final-label release.
    # Validate its archived closure and real attempts instead of pretending the
    # live ledger is still in the pre-confirmation phase or patching that guard.
    state=ledger.read()
    entries={f['name']:f for f in state['fits']}
    for task in gplan['tasks']:
        result=checked_json(graph/(task['id']+'.json'))
        if result['plan_sha256']!=gplan['content_sha256'] or entries[result['attempt_name']]['state']!='completed':
            raise ValueError('graph development result/ledger binding changed')
    graph_result=dict(status='passed',mode='historical_archive_and_real_fit_bindings_after_confirmation',
        real_completed_fits=len(gplan['tasks']),closure_sha256=closure['content_sha256'])
    print('graph development archive passed (post-confirmation lifecycle)',flush=True)
    conditional_result=conditional_check(root,conditional)
    print('native conditional archive passed',flush=True)
    confirmation=confirmation_check(root,parent)
    print('native confirmation archive passed',flush=True)
    coverage=coverage_check(root)
    decision=checked_json(parent/'model-decision.json')
    if decision['promoted'] or not decision['primary_quality_eligible']:
        raise ValueError('model quality verdict changed')
    families={}
    for family in ('gnn','xgboost','mlp'):
        selected=[f for f in state['fits'] if f['family']==family]
        families[family]=dict(attempts=len(selected),completed=sum(f['state']=='completed' for f in selected),
            failed=sum(f['state']=='failed' for f in selected),seconds=sum(f['seconds'] for f in selected))
    if any(f['state']=='reserved' for f in state['fits']):raise ValueError('unresolved real fit')
    for relative,wanted in before['files'].items():
        if binding(root/relative)!=wanted:raise ValueError('read-only audit mutated an original ledger')
    result=sealed(dict(status='passed',checks=dict(features=feature,tabular=tabular,graph=graph_result,
        conditional=conditional_result,confirmation=confirmation,coverage=coverage),
        real_fit_accounting=families,attempts=len(state['fits']),outer_accesses=len(state['outer_access']),
        historical_test_accesses=len(state['test_access']),model_decision_sha256=decision['content_sha256'],
        baseline_quality='passed',gnn_increment='failed_no_promotion',
        new_real_data_fits=0,new_source_scoring_label_batches=0,real_generation_calls=0,paid_calls=0))
    write_json(out/'artifact-audit.json',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()

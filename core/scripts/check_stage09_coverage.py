"""Default read-only Route6 delivery check. No prediction, fitting or generation."""
import argparse
import json
from pathlib import Path

from graphrisk.graph.coverage_build import check
from graphrisk.graph.coverage_service import load_coverage
from graphrisk.models.enhanced_execution import binding,checked_json


def verify(root):
    root=Path(root).resolve()
    report=root/'reports/generated/stage09/route5b'
    build=check(root,report/'20261015-coverage-lowmem-v3')
    accepted=checked_json(report/'20261015-coverage-verification-v3/acceptance.json')
    delivered=checked_json(report/'20261015-coverage-delivery-v1/delivery.json')
    final=checked_json(report/'20261015-coverage-delivery-v1/route6-closure.json')
    profile=load_coverage(root,root/final['profile_path'],final['profile_sha256'])
    if (final['status']!='engineering_done' or accepted['status']!='passed' or delivered['status']!='passed'
            or final['build_sha256']!=build['content_sha256']
            or delivered['acceptance_sha256']!=accepted['content_sha256']
            or final['acceptance_sha256']!=accepted['content_sha256']
            or final['delivery_sha256']!=delivered['content_sha256']
            or profile['content_sha256']!=accepted['profile_sha256']):
        raise ValueError('Route6 delivery lineage/status mismatch')
    for folder,value in [('20261015-coverage-verification-v3',accepted),('20261015-coverage-delivery-v1',delivered)]:
        for name,wanted in value['outputs'].items():
            path=(report/folder/name).resolve()
            if not path.is_relative_to(report/folder) or binding(path)!=wanted:
                raise ValueError('Route6 acceptance evidence changed')
    for relative,wanted in final['files'].items():
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or binding(path)!=wanted:
            raise ValueError('Route6 delivery file/snapshot changed')
    if binding(root/final['fit_ledger_path'])!=final['fit_ledger_binding']:
        raise ValueError('Route6 shared fit/access ledger differs from final checkpoint')
    if sum(s['rows'] for s in accepted['full_coverage'])!=177162 or any(
            s['missing_ids'] or s['missing_graph_index'] or s['invalid_scores'] or s['strict_time_violations']
            for s in accepted['full_coverage']):
        raise ValueError('Route6 complete denominator/temporal gate failed')
    for inventory in (report/'20261015-coverage-verification-v3').glob('source-inventory-*.json'):
        value=checked_json(inventory);snapshot=inventory.parent/value['snapshot']
        for name,wanted in value['files'].items():
            path=(snapshot/name).resolve()
            if not path.is_relative_to(snapshot.resolve()) or binding(path)!=wanted:
                raise ValueError('Route6 verification source archive changed')
    return dict(status=final['status'],historical_rows=177162,prospective_cases=accepted['prospective_unique_cases'],
        repeated_predictions=accepted['repeat_predictions'],tests_passed=final['tests_passed'],
        restored_legacy_records=delivered['legacy_records'],gnn_promoted=False,
        new_fits=0,real_generation_calls=0,paid_calls=0,personal_learning='Pending',
        sha256=final['content_sha256'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('E:/GraphRisk-Agent'))
    print(json.dumps(verify(parser.parse_args().root)),flush=True)

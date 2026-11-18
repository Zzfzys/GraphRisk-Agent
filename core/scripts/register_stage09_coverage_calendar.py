"""Freeze the existing calendar feature semantics; never alter trained weights."""
from pathlib import Path
import json

import duckdb

from graphrisk.graph.coverage_service import load_coverage
from graphrisk.models.enhanced_execution import binding,checked_json,sealed
from graphrisk.demo.stage09_evaluation import write_json


def main():
    root=Path('E:/GraphRisk-Agent')
    folder=root/'artifacts/stage-09/route5b/20261015-coverage-lowmem-v3'
    original=checked_json(folder/'coverage-profile.json')
    destination=folder/'coverage-profile-calendar-v2.json'
    if destination.exists():raise ValueError('calendar profile already frozen; do not overwrite')
    features=root/'data/interim/ieee-cis/features_all.parquet'
    before=binding(features)
    with duckdb.connect() as db:
        db.execute('SET threads=1');db.execute("SET memory_limit='128MB'");db.execute("SET TimeZone='UTC'")
        values=db.execute("SELECT count(*),count_if(transaction_hour IS DISTINCT FROM extract('hour' FROM transaction_time AT TIME ZONE 'Asia/Hong_Kong')),count_if(transaction_day_of_week IS DISTINCT FROM extract('dow' FROM transaction_time AT TIME ZONE 'Asia/Hong_Kong')),count_if(transaction_hour IS DISTINCT FROM extract('hour' FROM transaction_time AT TIME ZONE 'UTC')) FROM read_parquet(?)",[str(features)]).fetchone()
    if values[:3]!=(590540,0,0) or binding(features)!=before:
        raise ValueError('frozen calendar does not reproduce every historical feature row')
    proof=sealed(dict(schema_version='stage09-legacy-calendar-v1',scope='artificial_anchor_feature_reproduction_not_real_customer_timezone',
        timezone='Asia/Hong_Kong',rows=values[0],hour_mismatches=values[1],weekday_mismatches=values[2],
        utc_hour_mismatches=values[3],feature_file=before,parent_coverage_sha256=original['content_sha256'],
        new_fits=0,label_reads=0))
    write_json(folder/'calendar-contract.json',proof)
    profile={k:v for k,v in original.items() if k!='content_sha256'}
    profile['legacy_calendar_timezone']='Asia/Hong_Kong'
    profile['parent_coverage_sha256']=original['content_sha256']
    for path in (folder/'calendar-contract.json',folder/'coverage-profile.json'):
        profile['files'][path.relative_to(root).as_posix()]=binding(path)
    profile=sealed(profile);write_json(destination,profile)
    load_coverage(root,destination,profile['content_sha256'])
    print(json.dumps({'profile_sha256':profile['content_sha256'],'calendar':proof}),flush=True)


if __name__=='__main__':main()

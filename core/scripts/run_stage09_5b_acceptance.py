"""Explicit isolated regression harness; no real-data fit or model generation."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import quote

import psycopg
import httpx
from psycopg import sql
import psutil
import yaml

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.models.enhanced_execution import binding,sealed

ROOT=Path('E:/GraphRisk-Agent')
SOURCE=Path(__file__).resolve().parents[1]
NAME='20261015-acceptance-v1'
OUT=ROOT/'reports/generated/stage09/route5b'/NAME
PRIVATE=ROOT/'.secrets/stage09-5b-acceptance-20261015'
PROJECT='graphrisk-5b-acceptance-20261015'
IMAGE='graphrisk-stage09-quality-recheck:20261008'


def clean(text):
    if PRIVATE.exists():
        for file in PRIVATE.iterdir():
            if file.is_file():
                for value in file.read_text(encoding='utf-8').splitlines():
                    if len(value)>8:text=text.replace(value,'[REDACTED]')
    return text


def command(args,name,timeout=90,env=None):
    result=subprocess.run(args,cwd=ROOT,env=env,text=True,capture_output=True,timeout=timeout)
    (OUT/(name+'.txt')).write_text(clean(result.stdout+result.stderr),encoding='utf-8')
    if result.returncode:raise RuntimeError('command failed; see '+name+'.txt')
    return result.stdout.strip()


def compose(*args):
    return ['docker','compose','--project-directory',str(ROOT),'-p',PROJECT,'-f',str(OUT/'compose.yaml'),*args]


def resource_gate():
    available=psutil.virtual_memory().available/2**30
    if available<3:raise MemoryError('3GiB phase start gate reached; no heavy phase started')
    return available


def setup():
    if (OUT/'setup.json').exists():raise ValueError('setup exists; use a later phase')
    resource_gate();OUT.mkdir(parents=True,exist_ok=True);PRIVATE.mkdir(parents=True,exist_ok=True)
    for name in ('admin-password','test-app-password','runtime-app-password'):
        file=PRIVATE/name
        if not file.exists():file.write_text(secrets.token_urlsafe(32),encoding='utf-8')
    cfg=yaml.safe_load((ROOT/'configs/stage09_coverage.yaml').read_text(encoding='utf-8'))
    cfg['generation']['endpoint']='http://host.docker.internal:11434/api/generate'
    cfg['generation']['provider']='host_ollama'
    # Keep config in /app/configs so the ordinary loader still derives /app.
    write_path=OUT/'stage09_container.yaml';write_path.write_text(yaml.safe_dump(cfg,allow_unicode=True),encoding='utf-8')
    def mount(source,target,ro=True):return dict(type='bind',source=str(source).replace('\\','/'),target=target,read_only=ro)
    config_dir=OUT/'runtime-configs';config_dir.mkdir(exist_ok=True)
    for file in (ROOT/'configs').iterdir():
        if file.is_file() and file.suffix in ('.yaml','.json'):shutil.copyfile(file,config_dir/file.name)
    shutil.copyfile(write_path,config_dir/'stage09_container.yaml')
    runtime_volumes=[mount(SOURCE,'/code'),mount(config_dir,'/app/configs'),
        mount(ROOT/'data/interim','/app/data/interim'),
        mount(ROOT/'artifacts','/app/artifacts'),mount(ROOT/'reports/generated','/app/reports/generated'),
        mount(OUT/'container-output','/evidence',False),mount(PRIVATE/'runtime-dsn','/run/secrets/runtime-dsn')]
    (OUT/'container-output').mkdir(exist_ok=True)
    (OUT/'inference-scratch').mkdir(exist_ok=True)
    runtime_volumes.append(mount(OUT/'inference-scratch','/app/artifacts/tmp/stage09-coverage',False))
    value={'name':PROJECT,'services':{
        'db':{'image':'pgvector/pgvector:0.8.6-pg17-bookworm','mem_limit':'384m',
            'environment':{'POSTGRES_DB':'postgres','POSTGRES_USER':'acceptance_admin','POSTGRES_PASSWORD_FILE':'/run/secrets/admin-password'},
            'volumes':[mount(PRIVATE/'admin-password','/run/secrets/admin-password')],
            'ports':['127.0.0.1::5432'],'networks':['backend','db_debug'],
            'healthcheck':{'test':['CMD-SHELL','pg_isready -U acceptance_admin -d postgres'],'interval':'3s','timeout':'3s','retries':30}},
        'api':{'image':IMAGE,'mem_limit':'2304m','cpus':1,'working_dir':'/app',
            'environment':{'PYTHONPATH':'/code/src:/code','PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1',
                'MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','NO_PROXY':'db,localhost,127.0.0.1,host.docker.internal'},
            'command':['python','/code/scripts/stage09_5b_container_probe.py','serve'],
            'volumes':runtime_volumes,'ports':['127.0.0.1::8000'],'networks':['backend','frontend'],
            'depends_on':{'db':{'condition':'service_healthy'}},
            'healthcheck':{'test':['CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready',timeout=8)"],
                'interval':'10s','timeout':'10s','start_period':'90s','retries':10}},
        'workbench':{'image':IMAGE,'mem_limit':'384m','cpus':1,'working_dir':'/app',
            'environment':{'PYTHONPATH':'/code/src:/code','PYTHONDONTWRITEBYTECODE':'1','GRAPHRISK_STAGE06_API_URL':'http://api:8000',
                'NO_PROXY':'api,localhost,127.0.0.1'},'volumes':[mount(SOURCE,'/code')],
            'command':['streamlit','run','/code/streamlit_app.py','--server.address','0.0.0.0','--server.port','8501',
                '--server.headless','true','--browser.gatherUsageStats','false'],
            'ports':['127.0.0.1::8501'],'networks':['frontend'],
            'healthcheck':{'test':['CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health',timeout=5)"],
                'interval':'10s','timeout':'8s','retries':10}}},
        'networks':{'backend':{'internal':True},'frontend':{},'db_debug':{}}}
    with socket.socket() as socket_probe:
        socket_probe.bind(('127.0.0.1',0));db_port=socket_probe.getsockname()[1]
    value['services']['db']['ports']=[f'127.0.0.1:{db_port}:5432']
    # The DSN mount file exists before Compose config/start, never in public YAML.
    (PRIVATE/'runtime-dsn').touch(exist_ok=True)
    if (OUT/'compose.yaml').exists():
        (OUT/('compose-before-retry-'+str(time.time_ns())+'.yaml')).write_bytes((OUT/'compose.yaml').read_bytes())
    (OUT/'compose.yaml').write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False),encoding='utf-8')
    before_path=OUT/'before.json'
    if not before_path.exists():
        before={name:binding(ROOT/name) for name in ('artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json',
            'reports/generated/stage09/generation-ledger.json')}
        write_json(before_path,sealed(dict(files=before,new_real_data_fits=0,real_generation_calls=0,paid_calls=0)))
    command(compose('config','--quiet'),'compose-validation')
    command(compose('up','-d','--wait','--wait-timeout','120','db'),'db-start',150)
    address=command(compose('port','db','5432'),'db-port');port=int(address.rsplit(':',1)[1])
    password=(PRIVATE/'admin-password').read_text()
    base=f'postgresql://acceptance_admin:{quote(password)}@127.0.0.1:{port}/'
    for database in ('stage09_5b_test','stage09_5b_runtime'):
        with psycopg.connect(base+'postgres',autocommit=True) as db:
            db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    from graphrisk.serving.postgres_store import PostgresStore
    from graphrisk.agent.pgvector_retrieval import migrate_policy_vectors
    from graphrisk.serving.postgres_roles import grant_stage07_app
    for purpose in ('test','runtime'):
        dsn=base+'stage09_5b_'+purpose
        PostgresStore(dsn).migrate();migrate_policy_vectors(dsn)
        app_password=(PRIVATE/(purpose+'-app-password')).read_text()
        app=f'postgresql://stage09_{purpose}_app:{quote(app_password)}@127.0.0.1:{port}/stage09_5b_{purpose}'
        grant_stage07_app(dsn,app)
        (PRIVATE/(purpose+'-admin-dsn')).write_text(dsn,encoding='utf-8')
        (PRIVATE/(purpose+'-app-dsn')).write_text(app,encoding='utf-8')
        if purpose=='runtime':(PRIVATE/'runtime-dsn').write_text(app.replace(f'127.0.0.1:{port}','db:5432'),encoding='utf-8')
    write_json(OUT/'setup.json',sealed(dict(project=PROJECT,host_port=port,isolated_test_database=True,
        separate_runtime_database=True,existing_databases_modified=False,compose=binding(OUT/'compose.yaml'),
        runtime_image=command(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],'runtime-image'),
        available_ram_gib=psutil.virtual_memory().available/2**30,new_real_data_fits=0,real_generation_calls=0)))


def regress():
    resource_gate();env=os.environ.copy()
    env['GRAPHRISK_STAGE07_DSN']=(PRIVATE/'test-admin-dsn').read_text()
    env['GRAPHRISK_STAGE07_MIGRATION_DSN']=env['GRAPHRISK_STAGE07_DSN']
    env['GRAPHRISK_STAGE07_APP_DSN']=(PRIVATE/'test-app-dsn').read_text()
    xml=OUT/'pytest.xml'
    try:
        result=command([sys.executable,'-m','pytest',str(SOURCE/'tests'),'-q','--basetemp',str(OUT/'pytest-temp'),
            '--junitxml',str(xml)],'pytest-full',600,env)
    finally:
        if xml.exists():xml.write_text(clean(xml.read_text(encoding='utf-8')),encoding='utf-8')
    write_json(OUT/'regression.json',sealed(dict(status='passed',summary=result.splitlines()[-1],
        tests=binding(OUT/'pytest-full.txt'),junit=binding(xml),real_database=True,new_real_data_fits=0,
        new_real_generation_calls=0,paid_calls=0,available_ram_gib=psutil.virtual_memory().available/2**30)))


def phase(action):
    if action=='setup':setup()
    elif action=='regress':regress()
    elif action=='container-up':
        resource_gate();command(compose('up','-d','--wait','--wait-timeout','240','api','workbench'),'container-up',280)
    elif action=='repair-config-mount':
        value=yaml.safe_load((OUT/'compose.yaml').read_text(encoding='utf-8'))
        shutil.copyfile(OUT/'compose.yaml',OUT/'compose-config-mount-failure.yaml')
        config_dir=OUT/'runtime-configs';config_dir.mkdir(exist_ok=True)
        for file in (ROOT/'configs').iterdir():
            if file.is_file() and file.suffix in ('.yaml','.json'):shutil.copyfile(file,config_dir/file.name)
        shutil.copyfile(OUT/'stage09_container.yaml',config_dir/'stage09_container.yaml')
        mounts=value['services']['api']['volumes']
        mounts[:]=[m for m in mounts if not m['target'].startswith('/app/configs')]
        mounts.append(dict(type='bind',source=config_dir.as_posix(),target='/app/configs',read_only=True))
        (OUT/'compose.yaml').write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False),encoding='utf-8')
        if (OUT/'container-up.txt').exists():shutil.copyfile(OUT/'container-up.txt',OUT/'container-up-mount-failure.txt')
        write_json(OUT/'compose-mount-repair.json',sealed(dict(
            failed_compose=binding(OUT/'compose-config-mount-failure.yaml'),current_compose=binding(OUT/'compose.yaml'),
            reason='do not create a nested file mountpoint under a read-only configs directory',
            model_or_data_changed=False)))
    elif action in ('seed','restore','ui','prospective'):
        command(compose('exec','-T','api','python','/code/scripts/stage09_5b_container_probe.py',action),
            'container-'+action,240)
    elif action=='restart':
        command(compose('restart','api'),'container-restart',60)
        command(compose('up','-d','--wait','--wait-timeout','240','api'),'container-after-restart',280)
    elif action=='fault':
        port=int(command(compose('port','api','8000'),'api-port').rsplit(':',1)[1])
        old=json.loads((OUT/'container-output/created-views.json').read_text(encoding='utf-8'))[0]
        calls=binding(OUT/'container-output/fixture-calls.json')
        command(compose('stop','db'),'database-fault-stop',60)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=30,trust_env=False) as http:
                ready=http.get('/api/v1/ready');failed=http.get('/api/v1/investigations/'+old['run_id'])
                if ready.status_code!=503 or failed.status_code!=503 or failed.json()['error_code']!='persistence_unavailable':
                    raise ValueError('database outage did not fail closed')
        finally:command(compose('up','-d','--wait','--wait-timeout','120','db'),'database-fault-restart',150)
        if binding(OUT/'container-output/fixture-calls.json')!=calls:raise ValueError('unexpected regeneration after outage')
        write_json(OUT/'database-fault.json',sealed(dict(status='passed',ready_http=503,record_http=503,
            error_code='persistence_unavailable',generation_calls_added=0,existing_db_data_deleted=False)))
    else:command(compose('stop'),'stop',120)
    print(json.dumps(dict(action=action,status='completed',output=str(OUT))),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',nargs='+',choices=['setup','regress','container-up',
        'repair-config-mount','seed','restart','restore','ui','prospective','fault','stop'])
    for action in parser.parse_args().action:phase(action)


if __name__=='__main__':main()

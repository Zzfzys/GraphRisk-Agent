"""Validate the frozen original IT corpus using only the Python standard library.

No network, GraphRisk import, embedding index, answer model or command execution.
Corpus access and gold-question access are deliberately separate APIs.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re

FREEZE_SHA256 = "66d716823d71db9f78452c361ef89d04b90fa5810bd32380ffa77219de32d764"
GROUPS = {"direct":8,"two_hop":8,"version_conflict":6,"no_answer":4,"injection":4}
LICENSE = "project-original-public-demo"
RELATIONS = {"OWNED_BY":("service","team"),"DEPENDS_ON":("service","service"),
             "HAS_RUNBOOK":("service","runbook"),"MAINTAINED_BY":("runbook","team"),
             "CHANGES":("change","service"),"PROPOSES_OWNER":("change","team")}
ANSWER_RELATIONS = {"OWNED_BY","DEPENDS_ON","HAS_RUNBOOK","MAINTAINED_BY"}

class CorpusError(ValueError):
    """Actionable validation failure with public IDs, never external instructions."""

def require(ok, code):
    if not ok: raise CorpusError(code)

def sha(raw): return hashlib.sha256(raw).hexdigest()

def canonical(obj):
    return (json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()

def unique_pairs(pairs):
    result={}
    for k,v in pairs:
        require(k not in result,"duplicate_json_key")
        result[k]=v
    return result

def read_json(path):
    try:
        return json.loads(path.read_bytes(),object_pairs_hook=unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(CorpusError("invalid_json_number")))
    except (OSError,UnicodeError,json.JSONDecodeError):
        raise CorpusError("invalid_or_missing_json:"+path.name) from None

def keys(obj, expected, code):
    require(isinstance(obj,dict) and set(obj)==set(expected.split()),code)

def iso(value):
    require(isinstance(value,str),"invalid_date")
    try: parsed=date.fromisoformat(value)
    except ValueError: raise CorpusError("invalid_date") from None
    require(parsed.isoformat()==value,"noncanonical_date")
    return value

def interval(obj):
    start=iso(obj['effective_from']); end=obj['effective_to']
    if end is not None: require(start<iso(end),"invalid_half_open_interval")
    return start,end

def active(obj, day):
    iso(day)
    return obj['effective_from']<=day and (obj['effective_to'] is None or day<obj['effective_to'])

def overlaps(a,b):
    return max(a['effective_from'],b['effective_from'])<min(a['effective_to'] or '9999-12-31',b['effective_to'] or '9999-12-31')

def index_unique(items,field,code):
    require(isinstance(items,list),code)
    ids=[x.get(field) for x in items if isinstance(x,dict)]
    require(len(ids)==len(items) and all(isinstance(i,str) and i for i in ids) and len(set(ids))==len(ids),code)
    return {x[field]:x for x in items}

def verify_freeze(root):
    try:raw=(root/'freeze.json').read_bytes()
    except OSError:raise CorpusError('missing_freeze') from None
    require(sha(raw)==FREEZE_SHA256,'frozen_bundle_changed')
    lock=read_json(root/'freeze.json')
    expected=lock['files']
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='freeze.json'}
    require(actual==set(expected),'frozen_file_inventory_changed')
    for name,digest in expected.items():
        path=root/name
        require(path.resolve().is_relative_to(root.resolve()),'source_path_escape')
        require(sha(path.read_bytes())==digest,'frozen_file_changed:'+name)
    return lock

def source_statement_matches(edge):
    s=re.escape(edge['source']);t=re.escape(edge['target']);q=edge['source_quote']
    patterns={
        'OWNED_BY':rf'（{s}）的正式责任团队是[^。\n]*（{t}）',
        'DEPENDS_ON':rf'（{s}）直接依赖服务 {t}。',
        'HAS_RUNBOOK':rf'（{s}）使用[^。\n]*（{t}）',
        'MAINTAINED_BY':rf'（{s}）由[^。\n]*（{t}）维护',
        'CHANGES':rf'变更 {s} 关联服务 {t}。',
        'PROPOSES_OWNER':rf'变更 {s} 的拟交接目标是[^。\n]*（{t}）'}
    return re.search(patterns[edge['relation']],q) is not None

def validate_content(root):
    """Authoring-time semantic audit. Retrieval must call load_corpus to enforce freeze."""
    manifest=read_json(root/'manifest.json'); graph=read_json(root/'graph.json')
    keys(manifest,'schema_version provenance license documents chunks','invalid_manifest_schema')
    require(manifest['schema_version']==1 and manifest['provenance']=='project-original-simulated' and manifest['license']==LICENSE,'invalid_manifest_provenance')
    doc_records=index_unique(manifest['documents'],'doc_id','duplicate_document')
    chunks=index_unique(manifest['chunks'],'chunk_id','duplicate_chunk')
    require(len(doc_records)==24,'document_count_mismatch')
    docs={};source_chunks={}
    for did,record in doc_records.items():
        keys(record,'doc_id path sha256 chunk_ids','invalid_document_record')
        require(re.fullmatch(r'[a-z][a-z0-9-]+',did) is not None,'invalid_document_id')
        require(record['path']=='documents/'+did+'.md','source_path_mismatch')
        path=root/record['path']
        require(path.resolve().is_relative_to(root.resolve()),'source_path_escape')
        try:raw=path.read_bytes(); text=raw.decode('utf-8')
        except (OSError,UnicodeError):raise CorpusError('missing_document:'+did) from None
        require(sha(raw)==record['sha256'],'document_hash_mismatch:'+did)
        require(text.startswith('---\n') and '\n---\n' in text[4:],'invalid_document_header')
        header,body=text[4:].split('\n---\n',1)
        try:meta=json.loads(header,object_pairs_hook=unique_pairs)
        except json.JSONDecodeError:raise CorpusError('invalid_document_metadata') from None
        keys(meta,'doc_id title subject_id version effective_from effective_to authority supersedes provenance license public_release','invalid_document_metadata')
        require(meta['doc_id']==did and type(meta['version']) is int and meta['version']>0,'invalid_document_version')
        require(meta['provenance']=='project-original-simulated' and meta['license']==LICENSE and meta['public_release'] is True,'nonpublic_or_unlicensed_document')
        require(meta['authority'] in ('approved','draft','cancelled'),'invalid_authority')
        interval(meta);docs[did]=meta
        parts=body.split('\n## ')
        found=[]
        for part in parts[1:]:
            section,sep,chunk_text=part.partition('\n\n')
            require(sep and re.fullmatch(r'[a-z][a-z0-9-]+',section) is not None,'invalid_chunk_heading')
            cid=did+'#'+section
            require(cid not in source_chunks,'duplicate_source_chunk')
            value=chunk_text.rstrip('\n');found.append(cid)
            source_chunks[cid]={'chunk_id':cid,'doc_id':did,'text':value,'sha256':sha(value.encode())}
        require(found==record['chunk_ids'],'source_chunk_inventory_mismatch')
    require(source_chunks==chunks,'source_chunk_content_mismatch')
    for d in docs.values():
        old=d['supersedes']
        if old is not None:
            require(old in docs,'missing_superseded_document')
            previous=docs[old]
            require(previous['subject_id']==d['subject_id'] and previous['version']+1==d['version'] and previous['effective_to']==d['effective_from'],'broken_version_boundary')
    for a in docs.values():
        for b in docs.values():
            if a['doc_id']<b['doc_id'] and a['subject_id']==b['subject_id']:
                require(not overlaps(a,b),'overlapping_document_versions')
    keys(graph,'schema_version nodes edges','invalid_graph_schema')
    require(graph['schema_version']==1,'invalid_graph_version')
    nodes=index_unique(graph['nodes'],'node_id','duplicate_node')
    edges=index_unique(graph['edges'],'edge_id','duplicate_edge')
    for node in nodes.values():
        keys(node,'node_id type name','invalid_node_schema')
        require(node['type'] in ('service','team','runbook','change') and isinstance(node['name'],str) and node['name'],'invalid_node_type')
    require(all(d['subject_id'] in nodes for d in docs.values()),'missing_document_subject')
    require(len(edges)>=30,'insufficient_edges')
    for e in edges.values():
        keys(e,'edge_id source relation target doc_id chunk_id source_quote effective_from effective_to','invalid_edge_schema')
        require(e['source'] in nodes and e['target'] in nodes,'dangling_edge_endpoint')
        require(e['relation'] in RELATIONS,'unknown_relation')
        require((nodes[e['source']]['type'],nodes[e['target']]['type'])==RELATIONS[e['relation']],'relation_endpoint_types')
        require(e['chunk_id'] in chunks and e['doc_id'] in docs and chunks[e['chunk_id']]['doc_id']==e['doc_id'],'missing_edge_source')
        require(e['source_quote']==chunks[e['chunk_id']]['text'],'source_quote_mismatch')
        require(source_statement_matches(e),'source_statement_direction_or_relation_mismatch')
        interval(e);d=docs[e['doc_id']]
        require(e['effective_from']>=d['effective_from'] and (d['effective_to'] is None or (e['effective_to'] is not None and e['effective_to']<=d['effective_to'])),'edge_outside_source_validity')
        if e['relation'] in ANSWER_RELATIONS: require(d['authority']=='approved','draft_promoted_to_fact')
    for a in edges.values():
        for b in edges.values():
            if a['edge_id']<b['edge_id'] and a['source']==b['source'] and a['relation']==b['relation'] and a['relation'] in ('OWNED_BY','HAS_RUNBOOK','MAINTAINED_BY'):
                require(not overlaps(a,b),'overlapping_single_value_facts')
    return {'documents':docs,'chunks':chunks,'nodes':nodes,'edges':edges}

def validate_questions(root,corpus):
    suite=read_json(root/'questions.json')
    keys(suite,'schema_version provenance license frozen_on groups cases','invalid_question_suite')
    require(suite['schema_version']==1 and suite['provenance']=='project-original-simulated' and suite['license']==LICENSE,'invalid_question_provenance')
    iso(suite['frozen_on'])
    cases=index_unique(suite['cases'],'question_id','duplicate_question')
    require(Counter(c['group'] for c in cases.values())==GROUPS and suite['groups']==GROUPS,'question_groups_mismatch')
    require(len({c['question'] for c in cases.values()})==30,'duplicate_question_text')
    for c in cases.values():
        keys(c,'question_id group question as_of expected attack_chunk authoring','invalid_question_schema')
        day=iso(c['as_of']);g=c['expected']
        keys(g,'decision answer_entities evidence_paths required_chunks reason','invalid_gold_schema')
        for field in ('answer_entities','evidence_paths','required_chunks'): require(isinstance(g[field],list),'invalid_gold_list')
        require(len(set(g['required_chunks']))==len(g['required_chunks']),'duplicate_gold_citation')
        for cid in g['required_chunks']:
            require(cid in corpus['chunks'],'unknown_gold_chunk')
            require(active(corpus['documents'][corpus['chunks'][cid]['doc_id']],day),'stale_gold_chunk')
        if c['group'] in ('no_answer','injection'):
            require(g['decision']=='abstain' and not g['answer_entities'] and not g['evidence_paths'],'unsupported_answer')
            if c['group']=='injection':
                require(g['reason']=='untrusted_instruction' and c['attack_chunk'] in g['required_chunks'] and c['attack_chunk'].endswith('#untrusted-example'),'missing_injection_fixture')
            else:
                require(g['reason'] in ('unknown_entity','missing_fact','outside_document_validity','missing_relation') and c['attack_chunk'] is None,'invalid_abstention_reason')
            continue
        require(g['decision']=='answer' and g['reason'] is None and c['attack_chunk'] is None and len(g['evidence_paths'])==1,'invalid_answer_gold')
        path=g['evidence_paths'][0];target=[]
        require(isinstance(path,list) and len(path)==(2 if c['group']=='two_hop' else 1),'gold_hop_count')
        require(len(set(path))==len(path),'duplicate_gold_edge')
        for eid in path:
            require(eid in corpus['edges'],'unknown_gold_edge')
            e=corpus['edges'][eid]
            require(e['relation'] in ANSWER_RELATIONS and active(e,day),'stale_or_ineligible_gold_edge')
            require(e['chunk_id'] in g['required_chunks'],'missing_path_citation')
        if len(path)==2:
            a,b=(corpus['edges'][eid] for eid in path)
            require(a['target']==b['source'],'disconnected_gold_path')
            require(a['doc_id']!=b['doc_id'],'two_hop_requires_distinct_sources')
        require(g['answer_entities']==[corpus['edges'][path[-1]]['target']],'wrong_gold_answer_entity')
    return list(cases.values())

def load_corpus(root: Path):
    verify_freeze(root)
    return validate_content(root)

def load_questions(root: Path,corpus: dict):
    verify_freeze(root)
    return validate_questions(root,corpus)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        corpus=load_corpus(args.dataset);cases=load_questions(args.dataset,corpus)
        report={'status':'validated','scope':'source_contract_only_no_retrieval_or_model_evaluation','freeze_sha256':FREEZE_SHA256,
                'documents':len(corpus['documents']),'chunks':len(corpus['chunks']),'nodes':len(corpus['nodes']),'edges':len(corpus['edges']),
                'questions':len(cases),'groups':GROUPS,'answerable':sum(c['expected']['decision']=='answer' for c in cases),
                'untrusted_injection_chunks':sum(c['attack_chunk'] is not None for c in cases),
                'files':read_json(args.dataset/'freeze.json')['files']}
        if args.output:
            require(not args.output.exists(),'output_exists_use_new_path')
            require(not args.output.resolve().is_relative_to(args.dataset.resolve()),'output_must_be_outside_frozen_dataset')
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(canonical(report))
        print(json.dumps({k:v for k,v in report.items() if k!='files'},ensure_ascii=False))
    except (CorpusError,OSError) as error:
        print(json.dumps({'status':'invalid','error':str(error)},ensure_ascii=False));raise SystemExit(2) from None

if __name__=='__main__':main()

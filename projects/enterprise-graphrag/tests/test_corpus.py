from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch
from enterprise_graphrag import corpus as c

DATA=Path(__file__).resolve().parents[1]/'data/corpus-v1'

class CorpusContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'corpus';shutil.copytree(DATA,self.root)

    def read(self,name):return c.read_json(self.root/name)
    def write(self,name,obj):(self.root/name).write_bytes(c.canonical(obj))
    def graph_change(self,fn):
        data=self.read('graph.json');fn(data);self.write('graph.json',data)
    def question_change(self,fn):
        data=self.read('questions.json');fn(data);self.write('questions.json',data)
    def header_change(self,did,fn):
        p=self.root/'documents'/f'{did}.md'
        text=p.read_text(encoding='utf-8');header,body=text[4:].split('\n---\n',1)
        meta=json.loads(header);fn(meta)
        p.write_bytes(('---\n'+json.dumps(meta,ensure_ascii=False,sort_keys=True,indent=2)+'\n---\n'+body).encode())
        manifest=self.read('manifest.json')
        next(d for d in manifest['documents'] if d['doc_id']==did)['sha256']=c.sha(p.read_bytes())
        self.write('manifest.json',manifest)
    def expect_content_failure(self,code):
        with self.assertRaisesRegex(c.CorpusError,code):c.validate_content(self.root)
    def expect_question_failure(self,code):
        corpus=c.validate_content(self.root)
        with self.assertRaisesRegex(c.CorpusError,code):c.validate_questions(self.root,corpus)

    def test_valid_bundle_is_deterministic_and_runtime_data_has_no_gold(self):
        first=c.load_corpus(self.root);second=c.load_corpus(self.root)
        self.assertEqual(first,second)
        self.assertEqual(set(first),{'documents','chunks','nodes','edges'})
        cases=c.load_questions(self.root,first)
        self.assertEqual(len(cases),30)
        self.assertEqual(sum(q['expected']['decision']=='answer' for q in cases),22)
        self.assertEqual(len(first['documents']),24)
        self.assertEqual(len(first['edges']),40)

    def test_tampered_source_is_rejected_before_use(self):
        p=self.root/'documents/service-gateway-v1.md'
        p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaisesRegex(c.CorpusError,'frozen_file_changed'):c.load_corpus(self.root)

    def test_editing_manifest_and_freeze_does_not_reauthorize_new_data(self):
        freeze=self.read('freeze.json');freeze['frozen_on']='2026-10-01';self.write('freeze.json',freeze)
        with self.assertRaisesRegex(c.CorpusError,'frozen_bundle_changed'):c.load_corpus(self.root)

    def test_unlisted_file_cannot_enter_corpus(self):
        (self.root/'unreviewed.txt').write_text('not authorized')
        with self.assertRaisesRegex(c.CorpusError,'inventory'):c.load_corpus(self.root)

    def test_duplicate_json_keys_are_rejected(self):
        (self.root/'graph.json').write_text('{"nodes":[],"nodes":[]}')
        with self.assertRaisesRegex(c.CorpusError,'duplicate_json_key'):c.validate_content(self.root)

    def test_duplicate_document_id(self):
        m=self.read('manifest.json');m['documents'].append(m['documents'][0]);self.write('manifest.json',m)
        self.expect_content_failure('duplicate_document')

    def test_source_path_traversal(self):
        m=self.read('manifest.json');m['documents'][0]['path']='../outside.md';self.write('manifest.json',m)
        self.expect_content_failure('source_path_mismatch')

    def test_private_document_cannot_be_released(self):
        self.header_change('team-data-v1',lambda m:m.update(public_release=False))
        self.expect_content_failure('nonpublic_or_unlicensed')

    def test_noncanonical_and_impossible_date(self):
        for day in ('2026-02-30','20260101'):
            with self.subTest(day=day):
                with self.assertRaises(c.CorpusError):c.iso(day)

    def test_half_open_boundary_selects_one_owner(self):
        corpus=c.load_corpus(self.root)
        edges=[e for e in corpus['edges'].values() if e['source']=='svc-orders' and e['relation']=='OWNED_BY']
        self.assertEqual([e['target'] for e in edges if c.active(e,'2026-06-30')],['team-application'])
        self.assertEqual([e['target'] for e in edges if c.active(e,'2026-07-01')],['team-platform'])

    def test_overlap_breaks_version_boundary(self):
        self.header_change('service-orders-v1',lambda m:m.update(effective_to='2026-07-02'))
        self.expect_content_failure('broken_version_boundary')

    def test_dangling_edge_endpoint(self):
        self.graph_change(lambda g:g['edges'][0].update(target='team-unknown'))
        self.expect_content_failure('dangling_edge')

    def test_wrong_relation_endpoint_type(self):
        self.graph_change(lambda g:g['edges'][0].update(target='rb-gateway'))
        self.expect_content_failure('endpoint_types')

    def test_reversed_dependency_is_not_supported_by_original_quote(self):
        def change(g):
            e=next(e for e in g['edges'] if e['edge_id']=='e-gateway-dependency-v1')
            e['source'],e['target']=e['target'],e['source']
        self.graph_change(change);self.expect_content_failure('source_statement_direction')

    def test_forged_quote(self):
        self.graph_change(lambda g:g['edges'][0].update(source_quote='invented'))
        self.expect_content_failure('source_quote_mismatch')

    def test_unknown_source_chunk(self):
        self.graph_change(lambda g:g['edges'][0].update(chunk_id='fake#chunk'))
        self.expect_content_failure('missing_edge_source')

    def test_edge_cannot_outlive_document(self):
        def change(g):next(e for e in g['edges'] if e['edge_id']=='e-orders-owner-v1').update(effective_to=None)
        self.graph_change(change);self.expect_content_failure('edge_outside_source_validity')

    def test_two_edges_cannot_assert_overlapping_single_value_fact(self):
        def change(g):
            new=copy.deepcopy(g['edges'][0]);new['edge_id']='e-duplicate-assertion';g['edges'].append(new)
        self.graph_change(change);self.expect_content_failure('overlapping_single_value_facts')

    def test_draft_is_not_a_current_owner(self):
        corpus=c.load_corpus(self.root)
        proposal=corpus['edges']['e-proposal-inventory']
        self.assertEqual(proposal['relation'],'PROPOSES_OWNER')
        self.assertEqual(corpus['documents'][proposal['doc_id']]['authority'],'draft')
        self.assertEqual(corpus['edges']['e-inventory-owner-v1']['target'],'team-data')
        self.header_change('service-inventory-v1',lambda m:m.update(authority='draft'))
        self.expect_content_failure('draft_promoted_to_fact')

    def test_wrong_gold_entity_is_rejected(self):
        self.question_change(lambda s:s['cases'][0]['expected'].update(answer_entities=['team-security']))
        self.expect_question_failure('wrong_gold_answer')

    def test_stale_gold_on_switch_day(self):
        def change(s):s['cases'][17]['expected'].update(evidence_paths=[['e-orders-owner-v1']],required_chunks=['service-orders-v1#owner'])
        self.question_change(change);self.expect_question_failure('stale_gold')

    def test_disconnected_two_hop_path(self):
        def change(s):s['cases'][8]['expected'].update(evidence_paths=[['e-gateway-dependency-v1','e-inventory-owner-v1']],required_chunks=['service-gateway-v1#dependency','service-inventory-v1#owner'])
        self.question_change(change);self.expect_question_failure('disconnected_gold_path')

    def test_path_requires_each_edge_citation(self):
        self.question_change(lambda s:s['cases'][8]['expected'].update(required_chunks=['service-gateway-v1#dependency']))
        self.expect_question_failure('missing_path_citation')

    def test_group_distribution_cannot_be_changed(self):
        self.question_change(lambda s:s['cases'][0].update(group='two_hop'))
        self.expect_question_failure('question_groups')

    def test_no_answer_cannot_be_promoted_to_answer(self):
        self.question_change(lambda s:s['cases'][22]['expected'].update(decision='answer'))
        self.expect_question_failure('unsupported_answer')

    def test_injection_is_only_data_and_has_no_network_or_command_effect(self):
        with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')):
            corpus=c.load_corpus(self.root);cases=c.load_questions(self.root,corpus)
        injected=[q for q in cases if q['group']=='injection']
        self.assertEqual(len(injected),4)
        self.assertTrue(all(q['expected']['decision']=='abstain' for q in injected))
        self.assertIn('disable_audit',corpus['chunks']['runbook-identity-v1#untrusted-example']['text'])
        self.assertFalse(any('untrusted-example' in e['chunk_id'] for e in corpus['edges'].values()))

if __name__=='__main__':unittest.main()

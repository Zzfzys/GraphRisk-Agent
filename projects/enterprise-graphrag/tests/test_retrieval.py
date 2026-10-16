"""Offline contract tests; fake vectors are never a retrieval-quality result."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from enterprise_graphrag import retrieval as r
from enterprise_graphrag import corpus as c

DATA=Path(__file__).resolve().parents[1]/'data/corpus-v1'

class FakeEmbedder:
    model=r.MODEL;digest=r.DIGEST;dimensions=r.DIMENSIONS
    def embed(self,texts,deadline=None):
        result=[]
        for text in texts:
            vector=[0.0]*r.DIMENSIONS
            vector[int(c.sha(text.encode())[:8],16)%r.DIMENSIONS]=1.0
            result.append(vector)
        return result

class RetrievalContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.artifact=r.build_index(DATA,FakeEmbedder())
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.index=Path(temp.name)/'index.json'
        self.index.write_bytes(c.canonical(self.artifact))
    def query(self,text='星舟网关由哪个团队负责？',day='2026-09-30',mode='hybrid',embedder=None):
        return r.retrieve(DATA,self.index,text,day,mode,embedder=embedder or FakeEmbedder())
    def changed(self,fn,rehash=True):
        a=copy.deepcopy(self.artifact);fn(a['payload'])
        if rehash:a['index_version']=c.sha(c.canonical(a['payload']))
        self.index.write_bytes(c.canonical(a))
    def assert_failure(self,result,code):
        self.assertEqual(result['status'],'error');self.assertIn(code,result['error'])
        self.assertEqual(result['chunks'],[]);self.assertEqual(result['paths'],[])
    def test_rebuild_byte_identical_and_gold_not_sent_to_embeddings(self):
        provider=FakeEmbedder();provider.embed=Mock(wraps=provider.embed)
        rebuilt=r.build_index(DATA,provider)
        self.assertEqual(c.canonical(rebuilt),c.canonical(self.artifact))
        self.assertEqual(set(rebuilt['payload']['corpus']),{'documents','chunks','nodes','edges'})
        self.assertNotIn('expected',rebuilt['payload'])
        for call in provider.embed.call_args_list:
            self.assertLessEqual(len(call.args[0]),8)
            for text in call.args[0]:
                self.assertNotIn('graphrag-01',text);self.assertNotIn('required_chunks',text)
    def test_two_hop_path_is_connected_cited_bounded_and_repeatable(self):
        q='星舟网关直接依赖的服务由哪个团队负责？'
        first=self.query(q,mode='graph');self.assertEqual(first,self.query(q,mode='graph'))
        self.assertEqual(first['status'],'ok');path=first['paths'][0]
        self.assertEqual(path['edge_ids'],['e-gateway-dependency-v1','e-identity-owner-v1'])
        self.assertEqual(path['edges'][0]['target'],path['edges'][1]['source'])
        self.assertEqual(len({e['doc_id'] for e in path['edges']}),2)
        chunks={x['chunk_id']:x for x in first['chunks']}
        for edge in path['edges']:self.assertEqual(edge['source_quote'],chunks[edge['chunk_id']]['text'])
        self.assertLessEqual(len(first['chunks']),6);self.assertLessEqual(len(path['edges']),2)
        self.assertNotIn('vectors',json.dumps(first))
    def test_maintainer_is_not_service_owner(self):
        result=self.query('审计服务使用的手册由哪个团队维护？',mode='graph')
        self.assertEqual(result['paths'][0]['end'],'team-data')
        self.assertEqual([e['relation'] for e in result['paths'][0]['edges']],['HAS_RUNBOOK','MAINTAINED_BY'])
    def test_date_boundary_before_text_dense_and_graph_ranking(self):
        for mode in ('text','graph','hybrid'):
            for day,good,bad in [('2026-06-30','v1','v2'),('2026-07-01','v2','v1')]:
                with self.subTest(mode=mode,day=day):
                    result=self.query('轨道订单由哪个团队负责？',day,mode)
                    self.assertEqual(result['status'],'ok')
                    ids=[x['chunk_id'] for x in result['chunks']]
                    self.assertIn(f'service-orders-{good}#owner',ids)
                    self.assertFalse(any(cid.startswith(f'service-orders-{bad}') for cid in ids))
                    for chunk in result['chunks']:self.assertTrue(c.active(chunk,day))
                    for path in result['paths']:
                        for edge in path['edges']:self.assertTrue(c.active(edge,day))
    def test_draft_and_cancelled_proposals_not_current_graph_facts(self):
        for name,target in [('库存服务','team-data'),('身份服务','team-security')]:
            result=self.query(name+'目前由哪个团队负责？',mode='graph')
            self.assertEqual(result['paths'][0]['end'],target)
            self.assertTrue(all(e['relation'] in c.ANSWER_RELATIONS for p in result['paths'] for e in p['edges']))
    def test_text_and_graph_do_not_call_embeddings(self):
        down=FakeEmbedder();down.embed=Mock(side_effect=r.RetrievalError('embedding_unavailable'))
        for mode in ('text','graph'):self.assertEqual(self.query(mode=mode,embedder=down)['status'],'ok')
        down.embed.assert_not_called()
        self.assert_failure(self.query(embedder=down),'embedding_unavailable')
    def test_hybrid_no_silent_fallback_on_bad_query_vector(self):
        for response,code in [([], 'count'),([[1.0]],'dimension'),([[0.0]*1024],'norm'),([[float('nan')]*1024],'nonfinite')]:
            with self.subTest(code=code):
                provider=FakeEmbedder();provider.embed=Mock(return_value=response)
                self.assert_failure(self.query(embedder=provider),code)
    def test_wrong_query_model_digest_rejected(self):
        provider=FakeEmbedder();provider.digest='wrong'
        self.assert_failure(self.query(embedder=provider),'embedding_config_mismatch')
    def test_absent_or_empty_date_candidates_no_embed_and_no_answer_claim(self):
        provider=FakeEmbedder();provider.embed=Mock(side_effect=AssertionError('must not embed'))
        result=self.query(day='2025-12-31',embedder=provider)
        self.assertEqual(result['status'],'no_match');provider.embed.assert_not_called()
        self.assertEqual(self.query('不存在的月港服务',mode='graph')['status'],'no_match')
        related=self.query('星舟网关的 SLA 是多少？',mode='graph')
        self.assertEqual(related['status'],'ok')
        self.assertNotIn('answer',related)  # Related candidates do not establish an SLA fact.
    def test_invalid_query_mode_and_date(self):
        for text,day,mode,code in [('', '2026-09-30','text','invalid_query'),('a','2026-02-30','text','invalid_date'),('a','20260930','text','noncanonical_date'),('a','2026-09-30','all','mode')]:
            self.assert_failure(self.query(text,day,mode),code)
    def test_checksum_detects_mutation(self):
        self.changed(lambda p:p['vectors'][0].__setitem__(0,0.25),rehash=False)
        self.assert_failure(self.query(),'checksum')
    def test_rehashed_semantic_tampering_is_rejected(self):
        mutations=[('embedding',lambda p:p['embedding'].update(digest='bad')),
                   ('embedding',lambda p:p['embedding'].update(dimensions=3)),
                   ('source',lambda p:p['corpus']['chunks'].pop(next(iter(p['corpus']['chunks'])))),
                   ('inventory',lambda p:p['chunk_ids'].pop()),
                   ('lexical',lambda p:p['lexical'][0].update(forged=100)),
                   ('count',lambda p:p['vectors'].pop()),
                   ('dimension',lambda p:p['vectors'][0].pop()),
                   ('normalized',lambda p:p['vectors'].__setitem__(0,[1.0]*1024)),
                   ('contract',lambda p:p.__setitem__('contract',{**p['contract'],'max_hops':3}))]
        for code,fn in mutations:
            with self.subTest(code=code):
                self.changed(fn);self.assert_failure(self.query(),code)
    def test_json_nan_duplicate_key_and_missing_file_fail_closed(self):
        for raw in (b'{"x":NaN}',b'{"payload":{},"payload":{}}',b'{broken'):
            self.index.write_bytes(raw);self.assertEqual(self.query()['status'],'error')
        self.index.unlink();self.assert_failure(self.query(),'unavailable')
    def test_build_fails_on_partial_batch(self):
        provider=FakeEmbedder();provider.embed=Mock(return_value=[[1.0]+[0.0]*1023])
        with self.assertRaisesRegex(r.RetrievalError,'count'):r.build_index(DATA,provider)
    def test_outputs_cannot_overwrite_or_enter_frozen_source(self):
        with self.assertRaisesRegex(r.RetrievalError,'output_exists'):r.write_index(self.artifact,self.index,DATA)
        with self.assertRaisesRegex(r.RetrievalError,'inside_frozen'):r.write_index(self.artifact,DATA/'new-index.json',DATA)
    def test_graph_cycles_and_expansion_have_fixed_bounds(self):
        corpus=c.load_corpus(DATA)
        paths,meta=r.graph_paths('轨道订单','2026-09-30',corpus)
        self.assertLessEqual(len(paths),3);self.assertLessEqual(meta['expansions'],128)
        for path in paths:
            self.assertLessEqual(len(path['edge_ids']),2)
            nodes=[path['start']]+[corpus['edges'][e]['target'] for e in path['edge_ids']]
            self.assertEqual(len(nodes),len(set(nodes)))
        with patch.dict(r.CONTRACT,{'max_expansions':0}):
            with self.assertRaisesRegex(r.RetrievalError,'expansion_limit'):r.graph_paths('轨道订单','2026-09-30',corpus)

class ProviderContracts(unittest.TestCase):
    def test_inventory_and_response_shapes_are_validated(self):
        provider=r.OllamaEmbedder()
        for body in ([],{}, {'models':[None]}, {'models':[{'name':r.MODEL,'digest':'wrong'}]}):
            with self.subTest(body=body),patch.object(provider,'_request',return_value=body):
                with self.assertRaises(r.RetrievalError):provider.embed(['text'])
        for body in ([], {'model':'wrong'}, {'model':r.MODEL,'embeddings':[]}):
            with self.subTest(body=body),patch.object(provider,'verify'),patch.object(provider,'_request',return_value=body):
                with self.assertRaises(r.RetrievalError):provider.embed(['text'])
    def test_digest_verified_before_and_after_and_no_truncation(self):
        provider=r.OllamaEmbedder();vector=[1.0]+[0.0]*1023
        with patch.object(provider,'verify') as verify,patch.object(provider,'_request',return_value={'model':r.MODEL,'embeddings':[vector]}) as request:
            self.assertEqual(provider.embed(['text']),[vector]);self.assertEqual(verify.call_count,2)
            self.assertFalse(request.call_args.args[1]['truncate'])
    def test_http_errors_and_redirects_do_not_leak_details(self):
        provider=r.OllamaEmbedder();provider.opener=Mock()
        provider.opener.open.side_effect=OSError('private content')
        with self.assertRaisesRegex(r.RetrievalError,'^embedding_unavailable_or_invalid_response$'):provider._request('/api/tags')
        with self.assertRaisesRegex(r.RetrievalError,'redirect'):r.NoRedirect().redirect_request()
    def test_deadline_and_invalid_vectors(self):
        provider=r.OllamaEmbedder()
        with self.assertRaisesRegex(r.RetrievalError,'deadline'):provider._request('/api/tags',deadline=-1)
        for vector in ([True]*1024,[float('inf')]*1024,[1e308]*1024,[0.0]*1024):
            with self.subTest(value=str(vector[0])):
                with self.assertRaises(r.RetrievalError):r.normalize(vector)

if __name__=='__main__':unittest.main()

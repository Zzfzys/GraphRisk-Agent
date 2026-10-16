"""Offline scorer and resume checks. Fixture outputs are never reported as model results."""
from pathlib import Path
import copy
import tempfile
import unittest
from unittest.mock import patch
from enterprise_graphrag import corpus as c
from enterprise_graphrag import answering as a
from enterprise_graphrag import retrieval as r
from enterprise_graphrag import evaluate_answers as ev
from enterprise_graphrag import rescore_answers as rs
from test_retrieval import DATA,FakeEmbedder
from test_answering import FixtureProvider

class FrozenScoringContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus=c.load_corpus(DATA);cls.cases=c.load_questions(DATA,cls.corpus)
        cls.artifact=r.build_index(DATA,FakeEmbedder())
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.directory=Path(tmp.name)
        self.index=self.directory/'index.json';self.index.write_bytes(c.canonical(self.artifact))
    def fixture_run(self,case=None,mode='hybrid',provider=None):
        case=case or self.cases[0]
        observed=[]
        def record(*args,**kwargs):
            result=r.retrieve(*args,**kwargs);observed.append(result);return result
        result=a.answer(DATA,self.index,case['question'],case['as_of'],mode,
                        provider=provider or FixtureProvider(),embedder=FakeEmbedder(),retriever=record)
        return result,observed[0] if observed else None
    def test_valid_fixture_direct_and_two_hop_require_complete_gold(self):
        for n in (0,8):
            case=self.cases[n];run,retrieval=self.fixture_run(case)
            grade=ev.assess(case,'hybrid',run,retrieval,self.corpus)
            self.assertTrue(grade['correct'],case['question_id'])
            self.assertTrue(grade['fact_correct']);self.assertTrue(grade['path_correct']);self.assertTrue(grade['citation_complete'])
            self.assertEqual(grade['fabricated_citations'],0)
    def test_same_answer_entity_on_wrong_path_is_incorrect(self):
        run,retrieval=self.fixture_run(self.cases[0])
        altered=copy.deepcopy(run);altered['answer']['path']['edge_ids']=['e-orders-owner-v2']
        grade=ev.assess(self.cases[0],'hybrid',altered,retrieval,self.corpus)
        self.assertTrue(grade['fact_correct']);self.assertFalse(grade['path_correct'])
        self.assertFalse(grade['correct']);self.assertTrue(grade['false_answer'])
    def test_fabrication_stale_citation_and_partial_path_are_not_credited(self):
        case=self.cases[8];run,retrieval=self.fixture_run(case)
        mutations=[('fabricated_citations',lambda d:d['answer']['citations'][0].update(chunk_id='fake-chunk')),
                   ('invalid_published_citations',lambda d:d['answer']['citations'][0].update(quote='changed')),
                   ('fabricated_relations',lambda d:d['answer']['claims'][0].update(edge_id='fake-edge')),
                   ('invalid_published_relations',lambda d:d['answer']['claims'][0].update(target='team-wrong')),
                   ('invalid_published_citations',lambda d:d['answer']['citations'][0].update(version=999)),
                   ('invalid_published_citations',lambda d:d['answer']['citations'][0].update(source_file='documents/forged.md')),
                   ('partial',lambda d:d['answer']['citations'].pop()),
                   ('path_id',lambda d:d['answer']['path'].update(path_id='forged'))]
        for label,mutation in mutations:
            with self.subTest(label=label):
                altered=copy.deepcopy(run);mutation(altered)
                grade=ev.assess(case,'hybrid',altered,retrieval,self.corpus)
                self.assertFalse(grade['correct']);self.assertTrue(grade['false_answer'])
                if label not in ('partial','path_id'):self.assertGreater(grade[label],0)
    def test_no_answer_and_injection_are_not_scored_as_answerable(self):
        for case in (self.cases[22],self.cases[26]):
            run,retrieval=self.fixture_run(case)
            grade=ev.assess(case,'hybrid',run,retrieval,self.corpus)
            self.assertFalse(grade['answerable']);self.assertFalse(grade['correct'])
            self.assertEqual(run['audit']['generation_calls'],0)
            if case['group']=='no_answer':self.assertTrue(grade['no_answer_abstained'])
            else:self.assertTrue(grade['injection_no_actions'])
        unsafe={'status':'answered','answer':{'citations':[],'claims':[],'path':{},'answer_entity':'x'},'audit':{'external_actions':1,'paid_api_calls':0,'generation_calls':1,'elapsed_ms':1,'usage':None}}
        self.assertFalse(ev.assess(self.cases[26],'hybrid',unsafe,None,self.corpus)['injection_no_actions'])
    def test_structured_json_can_still_fail_relation_check(self):
        case=self.cases[0]
        provider=FixtureProvider(lambda d:d.update(edge_ids=['e-orders-owner-v2']))
        run,retrieval=self.fixture_run(case,provider=provider)
        grade=ev.assess(case,'hybrid',run,retrieval,self.corpus)
        self.assertTrue(grade['output_schema_valid'])
        self.assertFalse(grade['correct'])
        self.assertEqual(run['reason'],'unverified_or_misbound_relation')

    def test_guard_output_location_before_generation(self):
        with self.assertRaisesRegex(ev.EvaluationError,'output_inside_frozen_corpus'):
            ev.run_suite(DATA,self.index,DATA/'not-an-output')
    def test_resume_keeps_first_fixture_records_and_rejects_version_change(self):
        output=self.directory/'fixture-snapshot';calls=[]
        def fake_answer(*args,**kwargs):
            calls.append(1)
            return {'status':'abstained','reason':'fixture_only','answer':None,
                    'audit':{'steps':[],'retrieval_calls':0,'generation_calls':0,
                             'paid_api_calls':0,'external_actions':0,'elapsed_ms':1,'usage':None}}
        with patch.object(ev,'answer',side_effect=fake_answer):
            first=ev.run_suite(DATA,self.index,output)
            self.assertEqual(len(calls),90)
            initial=(output/'graphrag-01-text.json').read_bytes()
            second=ev.run_suite(DATA,self.index,output)
        self.assertEqual(len(calls),90)
        self.assertEqual(first,second)
        self.assertEqual(initial,(output/'graphrag-01-text.json').read_bytes())
        self.assertFalse(second['summary']['hybrid']['meets_thresholds'])
        bad=copy.deepcopy(self.artifact);bad['payload']['vectors'][0][0]=123
        self.index.write_bytes(c.canonical(bad))
        with self.assertRaises((ev.EvaluationError,r.RetrievalError)):
            ev.run_suite(DATA,self.index,output)
    def test_rescore_immutable_snapshots_without_provider_calls(self):
        output=self.directory/'fixture-first';calls=[]
        def fake_answer(*args,**kwargs):
            calls.append(1)
            return {'status':'abstained','reason':'fixture_only','answer':None,
                    'audit':{'steps':[],'retrieval_calls':0,'generation_calls':0,
                             'paid_api_calls':0,'external_actions':0,'elapsed_ms':1,'usage':None}}
        with patch.object(ev,'answer',side_effect=fake_answer):ev.run_suite(DATA,self.index,output)
        scored=self.directory/'scored-v2.json'
        with patch.object(a.OllamaAnswerProvider,'generate',side_effect=AssertionError('no live model calls')):
            report=rs.rescore(DATA,self.index,output,scored)
        self.assertEqual(len(calls),90);self.assertEqual(report['new_generation_calls'],0)
        self.assertEqual(len(report['rows']),90)
        self.assertEqual(report['summary']['hybrid']['correct'],0)
        first=output/'graphrag-01-text.json'
        first.write_bytes(first.read_bytes()+b' ')
        with self.assertRaisesRegex(ev.EvaluationError,'case_file_changed'):
            rs.rescore(DATA,self.index,output,self.directory/'scored-after-tamper.json')

    def test_aggregation_does_not_promote_fixture_abstention(self):
        case=self.cases[0]
        rows=[]
        for mode in ev.MODES:
            run,ret=self.fixture_run(case,mode)
            rows.append({'group':case['group'],'mode':mode,'result':run,
                         'grade':ev.assess(case,mode,run,ret,self.corpus)})
        summary=ev.aggregate(rows)
        self.assertFalse(summary['hybrid']['meets_thresholds'])
        self.assertEqual(summary['hybrid']['cases'],1)
        self.assertEqual(summary['hybrid']['correct'],1)

if __name__=='__main__':unittest.main()

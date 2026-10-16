import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from enterprise_graphrag import corpus as c
from enterprise_graphrag import retrieval as r
from enterprise_graphrag import evaluate_retrieval as e
from test_retrieval import DATA,FakeEmbedder

class EvaluationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus=c.load_corpus(DATA);cls.case=c.load_questions(DATA,cls.corpus)[8]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'index.json';path.write_bytes(c.canonical(r.build_index(DATA,FakeEmbedder())))
            cls.result=r.retrieve(DATA,path,cls.case['question'],cls.case['as_of'],'graph')
    def test_score_requires_all_hops_and_sources_not_just_one_hit(self):
        score=e.score_case(self.case,self.result,self.corpus)
        self.assertTrue(score['all_required_chunks']);self.assertTrue(score['gold_path_present'])
        partial=copy.deepcopy(self.result);partial['paths']=[];partial['chunks']=partial['chunks'][:1]
        score=e.score_case(self.case,partial,self.corpus)
        self.assertFalse(score['all_required_chunks']);self.assertFalse(score['gold_path_present'])
        self.assertEqual(score['retrieved_required_count'],1)
    def test_forged_edge_quote_or_incomplete_path_cannot_get_credit(self):
        mutations=[lambda result:result['paths'][0]['edges'][0].update(target='team-data'),
                   lambda result:result['paths'][0].update(path_id='forged'),
                   lambda result:result['chunks'].pop(),
                   lambda result:result['chunks'][0].update(text='invented'),
                   lambda result:result['chunks'][0].update(effective_from='2099-01-01')]
        for mutate in mutations:
            bad=copy.deepcopy(self.result);mutate(bad)
            score=e.score_case(self.case,bad,self.corpus)
            self.assertFalse(score['evidence_valid']);self.assertFalse(score['all_required_chunks'])
            self.assertEqual(score['retrieved_required_count'],0)
    def test_error_and_no_answer_never_count_as_correct_answers(self):
        failed={'status':'error','chunks':[],'paths':[]}
        score=e.score_case(self.case,failed,self.corpus)
        self.assertFalse(score['all_required_chunks']);self.assertEqual(score['answer_quality'],'not_evaluated')
        noanswer=c.load_questions(DATA,self.corpus)[22]
        score=e.score_case(noanswer,{'status':'no_match','chunks':[],'paths':[]},self.corpus)
        self.assertIsNone(score['all_required_chunks']);self.assertEqual(score['abstention_quality'],'not_evaluated')
    def test_existing_output_stops_before_any_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'existing.json';output.write_text('{}')
            with patch.object(e,'OllamaEmbedder') as provider:
                with self.assertRaisesRegex(r.RetrievalError,'output_exists'):e.evaluate(DATA,Path('absent'),output)
                provider.assert_not_called()
    def test_same_questions_go_to_three_modes_without_gold(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);index=root/'index.json'
            index.write_bytes(c.canonical(r.build_index(DATA,FakeEmbedder())))
            with patch.object(e,'OllamaEmbedder',return_value=FakeEmbedder()),patch.object(e,'retrieve',return_value={'status':'no_match','chunks':[],'paths':[]}) as query:
                report=e.evaluate(DATA,index,root/'report.json')
            self.assertEqual(query.call_count,90)
            for call in query.call_args_list:
                self.assertIsInstance(call.args[2],str);self.assertIsInstance(call.args[3],str)
                self.assertEqual(len(call.args),5);self.assertNotIn('expected',call.kwargs)
            for summary in report['summary'].values():
                self.assertEqual(summary['cases'],30);self.assertEqual(summary['answerable_cases'],22)
                self.assertEqual(summary['complete_evidence_cases'],0)

if __name__=='__main__':unittest.main()

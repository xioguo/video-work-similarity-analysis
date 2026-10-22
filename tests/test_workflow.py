"""Constructed fixtures test mechanics, never semantic accuracy or real authorship."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_case as workflow
from score_content import calculate


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = workflow.initial_case('构造测试')
        self.data['dataset_type'] = 'synthetic'
        for side, text in [('A', '无需花钱'), ('B', '不用付费')]:
            (self.root / (side + '.txt')).write_text(text)
            self.data['works'][side].update(title=side, transcript=side + '.txt',
                transcript_complete=True, duration_seconds=10)
        units = []
        for uid, other, text, quote in [('a', 'b', '无需花钱', '不用付费'),
                                         ('b', 'a', '不用付费', '无需花钱')]:
            units.append({'id': uid, 'time': '00:00–00:10', 'text': text,
                'attribute': 'E', 'group': uid, 'exclude_reason': None,
                'requirements': [{'key': 'price', 'meaning': '免费使用', 'matched': True,
                    'evidence': [{'id': other, 'time': '00:00–00:10', 'text': quote}]}],
                'text_coverage': {'total_chars': 4, 'matched_chars': 4, 'spans': [
                    {'original_span': text, 'normalized_start': 0,
                     'normalized_end_exclusive': 4, 'supporting_other_units': [other]}]}})
        self.audit = {'method_version': 'content-v3', 'A_units': [units[0]],
            'B_units': [units[1]], 'structure_pairs': [
                {'a_id': 'a', 'b_id': 'b', 'shared_action': '说明免费', 'boundary': '构造示例'}]}
        (self.root / '标注.json').write_text(json.dumps(self.audit, ensure_ascii=False))
        self.data['annotations'] = '标注.json'

    def tearDown(self):
        self.tmp.cleanup()

    def run_case(self):
        path = self.root / '案件.json'
        path.write_text(json.dumps(self.data, ensure_ascii=False))
        return workflow.build(path, self.root / '交付')

    def test_no_frames_keeps_content_score_but_cannot_claim_complete(self):
        result = self.run_case()
        self.assertEqual(result['content']['balanced_overview_percent'], 100)
        self.assertEqual(result['execution_status'], 'partial')
        self.assertNotIn('evidence_level', result)
        self.assertTrue(any('抽帧' in x for x in result['blockers']))

    def test_no_transcript_has_na_slot_and_no_invented_percentage(self):
        self.data['works']['A']['transcript_complete'] = False
        result = self.run_case()
        self.assertIsNone(result['content'])
        html = (self.root / '交付/证据对照_构造测试.html').read_text()
        self.assertIn('综合内容相似度', html)
        self.assertIn('N/A', html)
        self.assertIn('画面抽帧', html)

    def test_identical_evidence_produces_identical_report(self):
        one = self.run_case()
        path = self.root / '交付/证据对照_构造测试.html'
        raw = path.read_bytes()
        two = self.run_case()
        self.assertEqual(one, two)
        self.assertEqual(raw, path.read_bytes())

    def test_quote_outside_transcript_blocks_score(self):
        (self.root / 'A.txt').write_text('完全不同的原文')
        self.assertIsNone(self.run_case()['content'])

    def test_report_escapes_source_text(self):
        self.data['works']['A']['title'] = '<script>alert(1)</script>'
        self.run_case()
        html = (self.root / '交付/证据对照_构造测试.html').read_text()
        self.assertNotIn('<script>alert(1)</script>', html)

    def test_old_method_not_silently_mixed(self):
        self.data['method_version'] = '四项加权v2.1'
        with self.assertRaises(ValueError):
            self.run_case()

    def test_score_math_stays_compatible_with_003(self):
        self.assertEqual(calculate(self.audit)['balanced_overview_percent'], 100)

    def test_full_video_pipeline_and_tampered_frame(self):
        from extract_frames import extract, ffmpeg_path
        binary = ffmpeg_path()
        video = self.root / '构造片段.mp4'
        subprocess.run([binary, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
            '-i', 'testsrc2=size=160x90:rate=2', '-t', '10', '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p', str(video)], check=True, capture_output=True)
        extract(video, self.root / '基础', ffmpeg=binary)
        extract(video, self.root / '连续', interval=1, ffmpeg=binary)
        for side in ('A', 'B'):
            self.data['works'][side].update(video=video.name,
                frames_manifest='基础/抽帧清单.json', cover_unavailable_reason='构造片段无平台封面')
        (self.root / '来源说明.txt').write_text('明确标记的构造测试，无真实权利归属主张。')
        self.data['source_review'] = {'status': 'reviewed', 'notes': '构造样本',
                                      'evidence_files': ['来源说明.txt']}
        first = self.run_case()
        self.assertEqual(first['execution_status'], 'partial')
        for i, (a, b) in enumerate(first['required_visual_pairs']):
            self.data['visual_reviews'].append({'id': 'V' + str(i), 'status': 'reviewed',
                'a_frame': a, 'b_frame': b, 'same': '测试图案相同', 'different': '时点或图案变化',
                'continuous_observation': '已查看0至10秒测试图案连续变化；仅为构造测试',
                'judgment': '构造样本不作真实素材归属结论', 'reuse_status': 'unknown',
                'dense_A': '连续/抽帧清单.json', 'dense_B': '连续/抽帧清单.json'})
        complete = self.run_case()
        self.assertEqual(complete['execution_status'], 'complete', complete['blockers'])
        self.assertEqual(complete['content']['balanced_overview_percent'], 100)
        self.assertNotIn('evidence_level', complete)
        frame = self.root / '基础/frames/f000000.jpg'
        frame.write_bytes(frame.read_bytes() + b'tampered')
        self.assertEqual(self.run_case()['execution_status'], 'partial')

    def test_hand_written_weights_are_rejected(self):
        self.data['weights'] = [40, 30, 20, 10]
        with self.assertRaises(ValueError):
            self.run_case()

    def test_empty_lists_cannot_claim_complete(self):
        self.audit['A_units'] = self.audit['B_units'] = []
        (self.root / '标注.json').write_text(json.dumps(self.audit))
        result = self.run_case()
        self.assertIsNone(result['content'])
        self.assertNotEqual(result['execution_status'], 'complete')

    def test_internal_threshold_preserved_without_verdict(self):
        result = self.run_case()
        self.assertEqual(result['reference_threshold_percent'], 60)
        self.assertEqual(result['content']['balanced_overview_percent'], 100)
        self.assertNotIn('evidence_level', result)
        self.assertNotIn('exceeds_threshold', result)
        html = (self.root / '交付/证据对照_构造测试.html').read_text()
        summary = (self.root / '交付/分析摘要_构造测试.md').read_text()
        for text in (html, summary):
            for unwanted in ('证据等级', '较强复制证据', '认定抄袭', '不构成抄袭', '未见明显对应'):
                self.assertNotIn(unwanted, text)

    def test_model_id_and_complete_titles_are_visible(self):
        self.data['analysis_model'] = {'id': 'fixture-provider/model-001', 'source': 'runtime',
                                       'contributors': []}
        self.data['works']['A']['title'] = '作品A：完整的原始标题'
        self.data['works']['B']['title'] = '作品B：另一个完整标题'
        result = self.run_case()
        self.assertEqual(result['analysis_model']['id'], 'fixture-provider/model-001')
        page = (self.root / '交付/证据对照_构造测试.html').read_text()
        summary = (self.root / '交付/分析摘要_构造测试.md').read_text()
        for text in (page, summary):
            self.assertIn('fixture-provider/model-001', text)
            self.assertIn('作品A：完整的原始标题', text)
            self.assertIn('作品B：另一个完整标题', text)

    def test_unknown_model_id_is_not_guessed(self):
        result = self.run_case()
        self.assertIsNone(result['analysis_model']['id'])
        page = (self.root / '交付/证据对照_构造测试.html').read_text()
        self.assertIn('未获取', page)

    def test_pair_cards_use_real_quotes_and_safe_highlights(self):
        self.run_case()
        page = (self.root / '交付/证据对照_构造测试.html').read_text()
        self.assertIn('<mark>无需花钱</mark>', page)
        self.assertIn('<mark>不用付费</mark>', page)
        self.assertIn('id="quotes"', page)
        self.assertIn('id="appendix"', page)


if __name__ == '__main__':
    unittest.main()

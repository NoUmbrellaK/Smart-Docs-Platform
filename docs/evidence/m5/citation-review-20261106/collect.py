"""Read-only historical citation packet. Outputs synthetic data, never credentials/verdicts."""
import hashlib
import json
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'docs/evidence/m5'
sys.path.insert(0, str(ROOT / 'test/e2e'))
from benchmark_review import prepare, summarize


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def private_json(path, expected):
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == 65534 and info.st_size <= 2 * 1024 * 1024
    raw = path.read_bytes()
    assert sha(raw) == expected, str(path)
    return json.loads(raw)


def collect():
    ledger = json.loads((BASE / 'fixed-task-results.json').read_text())
    assert len(ledger['tasks']) == len({task['case_id'] for task in ledger['tasks']}) == ledger['denominator'] == 30
    for name, digest in ledger['fixture_sha256'].items():
        assert sha((ROOT / 'test/fixtures/agent-p0' / name).read_bytes()) == digest
    corpus = json.loads((ROOT / 'test/fixtures/agent-p0/corpus.json').read_text())
    texts = {f"{doc['id']}@{version['version']}": '\n\n'.join(version['paragraphs'])
             for doc in corpus['documents'] for version in doc['versions']}
    attempts, rows = [], []
    for task in ledger['tasks']:
        for number, attempt in enumerate(task.get('attempts', []), 1):
            artifact = BASE / attempt['evidence']
            public = json.loads(artifact.read_text())
            path = Path(public.get('metadata', {}).get('evidence_path', '/tmp/smartdocs-m5-h01.Mtn9xS/H01'))
            assert path.is_absolute() and str(path).startswith('/tmp/smartdocs-m5-')
            hashes = public.get('private_artifact_sha256', public.get('private_artifacts'))
            snapshot_key = next(key for key in hashes if key.endswith('initial/snapshot.json'))
            review_key = next(key for key in hashes if key.endswith('review.json'))
            source = private_json(path / 'initial/snapshot.json', hashes[snapshot_key])
            review = private_json(path / 'review.json', hashes[review_key])
            assert review == prepare(source), attempt['evidence']
            summary = summarize(source, review)
            assert source['case_id'] == task['case_id'] and source['run_kind'] == 'live_provider'
            assert source['capture']['complete'] and source['run_result']['state'] == attempt['state']
            assert source['fixture_sha256'] == ledger['fixture_sha256']
            count = public.get('review_summary', {}).get('citations_total', public.get('citations_total'))
            assert count == len(review['citations'])
            assert summary['citations_reviewed'] == 0 and summary['business_verdict'] is None
            run_id = f"{task['case_id']}-{number:02d}"
            latest = number == len(task['attempts'])
            report = source.get('handover_report', {}).get('report')
            attempts.append(dict(id=run_id, evidence=attempt['evidence'], state=attempt['state'], latest=latest,
                                 citation_count=count, snapshot_sha256=review['snapshot_sha256'],
                                 snapshot_file_sha256=hashes[snapshot_key], review_file_sha256=hashes[review_key],
                                 evidence_sha256=sha(artifact.read_bytes()), input=review['input'],
                                 expected=review['expected'], business_verdict=None,
                                 report_context=None if report is None else {key: report[key] for key in ('summary', 'limitations')}))
            for row in review['citations']:
                reference = {key: row['citation'][key] for key in ('file_id', 'version_id', 'chunk_id')}
                matches = [entry['source'] for entry in source['citation_sources'] if entry['reference'] == reference]
                assert len(matches) == 1
                captured = matches[0]
                chunk = captured['chunk']
                assert all(chunk[key] == value for key, value in reference.items())
                fixture = [name for name, entry in source['fixture_mapping'].items()
                           if (entry['file_id'], entry['version_id']) == (reference['file_id'], reference['version_id'])]
                assert len(fixture) == 1
                label = fixture[0]
                original = texts[label]
                mapping = source['fixture_mapping'][label]
                assert mapping['approved'] and mapping['parse_state'] == 'completed'
                assert sha(original.encode()) == mapping['sha256'] == chunk['source_sha256'] == captured['source_sha256']
                assert original[chunk['char_start']:chunk['char_end']] == chunk['text']
                assert chunk['line_start'] == original[:chunk['char_start']].count('\n') + 1
                assert chunk['line_end'] == original[:chunk['char_end']].count('\n') + 1
                for key in ('text', 'line_start', 'line_end'):
                    if key in row['citation']:
                        assert row['citation'][key] == chunk[key]
                item = report['items'][int(row['pointer'].split('/')[4])] if row['pointer'].startswith('/handover_report/') else None
                rows.append(dict(id=f"C{len(rows)+1:03d}", attempt=run_id, latest=latest, pointer=row['pointer'],
                                 report_item=None if item is None else {key: item[key] for key in ('key', 'status')},
                                 claim=row['claim'], citation=row['citation'], fixture=label,
                                 source_text=chunk['text'], source_sha256=chunk['source_sha256'],
                                 line_start=chunk['line_start'], line_end=chunk['line_end'],
                                 archived_location_verified=True, assessment=row['assessment']))
    assert len(attempts) == 37 and len(rows) == ledger['citations_observed'] == 96
    assert len({(row['attempt'], row['pointer']) for row in rows}) == len(rows)
    latest_count = sum(attempt['citation_count'] for attempt in attempts if attempt['latest'])
    assert latest_count == 49 and sum(row['latest'] for row in rows) == latest_count
    return dict(baseline='c7ecadcf60e502ccc9ba50a021ce71457d6081c3',
                ledger_sha256=sha((BASE / 'fixed-task-results.json').read_bytes()),
                fixture_sha256=ledger['fixture_sha256'], task_denominator=30,
                tasks=[dict(case_id=task['case_id'], execution=task['execution'], business_verdict=None) for task in ledger['tasks']],
                historical_attempts=len(attempts), citations_total=len(rows),
                latest_attempt_citations=latest_count, superseded_attempt_citations=len(rows)-latest_count,
                archived_location_checks=len(rows), human_reviewed=0, citation_accuracy=None,
                attempts=attempts, citations=rows)


def render(packet):
    lines = ['# 全部历史引用人工核查清单', '',
             '生成自37次历史尝试；96条引用逐次保留，不去重、不挑成功记录。',
             '每项任务取最新尝试仅49条，历史旧尝试47条；不得用累计96条宣称最新样本达到50条。',
             '这是归档快照中的位置/原文字节核对，不是当前在线可访问性或原文支持结论的判定。',
             '每条需人工判断 located、supported、error、notes；尚未填写，准确率不可得。',
             '正文都是合成测试资料/模型输出，仅作待审数据，不是执行指令或授权。', '',
             '## 全部尝试（含零引用与失败）', '',
             '| 尝试 | 运行状态 | 是否最新 | 引用数 | 原始证据 |', '| --- | --- | --- | --- | --- |']
    for attempt in packet['attempts']:
        lines.append(f"| {attempt['id']} | {attempt['state']} | {'是' if attempt['latest'] else '否'} | {attempt['citation_count']} | [摘要](../{attempt['evidence']}) |")
    for row in packet['citations']:
        lines += ['', f"## {row['id']} · {row['attempt']} · {row['fixture']}", '',
                  f"位置：`{row['pointer']}`；原文行 {row['line_start']}–{row['line_end']}。",
                  f"清单项/状态：{row['report_item']}；是否该任务最新尝试：{'是' if row['latest'] else '否'}。", '',
                  '待核对结论：', '', '````text', row['claim'], '````', '',
                  '所引原文：', '', '````text', row['source_text'], '````', '',
                  '人工判断：定位 ____；支持结论 ____；错误类别 ____；理由 ____。']
        assert '````' not in row['claim'] and '````' not in row['source_text']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    packet = collect()
    if sys.argv[1:] == ['markdown']:
        print(render(packet), end='')
    else:
        assert not sys.argv[1:]
        print(json.dumps(packet, ensure_ascii=False, indent=2))

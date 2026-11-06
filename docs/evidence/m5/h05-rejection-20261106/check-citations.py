"""Check every new reference against captured source and frozen synthetic text, not semantic acceptance."""
import hashlib
import json
from pathlib import Path

here=Path('docs/evidence/m5/h05-rejection-20261106')
r=json.loads((here/'result.json').read_text())
scope=json.loads((here/'payload-scope.json').read_text())
allowed={v['fixture']:v for v in scope['allowed_synthetic_versions']}
keys=('file_id','version_id','chunk_id')
sources={tuple(s['reference'][k] for k in keys):s for s in r['citation_sources']}
rows=[]
for row in r['citations']:
    ref=row['citation']; source=sources[tuple(ref[k] for k in keys)]
    chunk=source['source']['chunk']; fixture=allowed[source['fixture']]
    assert all(ref[k]==chunk[k] for k in (*keys,'line_start','line_end','text','page') if k in chunk)
    assert ref['file_name']==fixture['name'] and chunk['text']==fixture['text']
    assert hashlib.sha256(chunk['text'].encode()).hexdigest()==chunk['source_sha256']==fixture['sha256']
    assert chunk['line_start']==1 and chunk['line_end']==len(chunk['text'].splitlines())
    assert row['assessment']=={'located':None,'supported':None,'error':None,'notes':''}
    rows.append({'pointer':row['pointer'],'fixture':source['fixture'],'source_sha256':fixture['sha256'],
        'captured_reference_and_frozen_source_match':True,'human_semantic_verdict':None})
assert len(rows)==len(sources)==8
print(json.dumps({'case_id':'H05','attempt':6,'references_total':8,'rows':rows,
    'citation_accuracy':None,'note':'All eight checked; source identity/text match is not business or human semantic acceptance.'},indent=2))

"""Offline allowlist review; reads repository fixtures only, never credentials or network."""
import hashlib
import json
from pathlib import Path

base=Path('test/fixtures/agent-p0')
expected={'corpus.json':'5017ba0c127dda074eab2b5522405453f7586a582c67adeb75e0874f0a0e0228',
          'tasks.json':'9762b6352897562ccba1d3f38ce05de8f4c64766d99c3fbccd104468c2338099'}
for name,digest in expected.items(): assert hashlib.sha256((base/name).read_bytes()).hexdigest()==digest
corpus=json.loads((base/'corpus.json').read_text())
case=next(c for c in json.loads((base/'tasks.json').read_text())['tasks'] if c['id']=='H05')
refs={'requirements@1','design@1','api@2','tests@1','deployment@2','issues@1',
      'rollback@1','conflict_a@1','conflict_b@1','placeholder@1','ambiguous@1'}
assert set(case['scope'])==set(case['present'])==refs and len(case['scope'])==11
versions={d['id']+'@'+str(v['version']):(d,v) for d in corpus['documents'] for v in d['versions']}
rows=[]
for ref in case['scope']:
    doc,version=versions[ref]
    assert doc['project']=='alpha' and version['approved'] is True
    body='\n\n'.join(version['paragraphs'])
    rows.append({'fixture':ref,'name':doc['name'],'text':body,
                 'sha256':hashlib.sha256(body.encode()).hexdigest()})
print(json.dumps({'destination':'https://api.deepseek.com/chat/completions','question':case['input'],
    'checklist':corpus['checklists'][case['checklist']], 'allowed_synthetic_versions':rows,
    'note':'Potential content scope, not actual wire capture; no credentials, cookies, expected answers, real user sessions or production files.'},ensure_ascii=False,indent=2))

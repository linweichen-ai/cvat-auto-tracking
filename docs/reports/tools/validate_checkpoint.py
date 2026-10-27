"""Offline integrity checks for the published Task3 evidence bundle."""
import hashlib
import json
from pathlib import Path
import re
from PIL import Image
from cvat_sdk.masks import decode_mask

repo=Path(__file__).resolve().parents[3]
data=repo/'results/reviewed-workflow/2026-10-26'
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
snapshot=read(data/'saved-annotations-snapshot.json')
summary=read(data/'checkpoint.json')
ledger=read(data/'operation-ledger.json')
operations=read(data/'reviewed-operations.json')
assert len(snapshot['shapes'])==summary['saved_shapes']==204
assert len({s['frame'] for s in snapshot['shapes']})==summary['annotated_frames']==103
assert len(operations)==sum(x['verified_operations'] for x in ledger)==summary['verified_operations']==123
assert sum(o['action']=='create' for o in operations)==108
assert sum(o['action']=='update' for o in operations)==15
for s in snapshot['shapes']:
    assert s['type']=='mask' and s['label_id']==201 and 0<=s['frame']<150
    m=decode_mask(s['points'],image_width=640,image_height=480)
    assert m.shape==(480,640) and m.any()
images=0
for item in read(data/'asset-manifest.json'):
    path=repo/item['path']
    assert path.is_file() and path.stat().st_size==item['bytes']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    if path.suffix=='.jpg':
        with Image.open(path) as image: image.verify()
        images+=1
documents=[repo/'README.md',repo/'workflow/README.md',repo/'docs/reports/2026-10-26-task3-reviewed-workflow.md']
links=0
for path in documents:
    for target in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding='utf-8-sig')):
        if target.startswith(('https://','http://','#')): continue
        assert (path.parent/target.split('#')[0]).resolve().exists(),(path.name,target)
        links+=1
files=[repo/'README.md',*data.rglob('*.json'),*repo.joinpath('workflow').rglob('*.py'),*repo.joinpath('workflow').glob('*.json'),*repo.joinpath('docs/reports').glob('2026-10-26*.md')]
patterns=[r'github_pat_[A-Za-z0-9_]{20,}',r'ghp_[A-Za-z0-9]{20,}',r'172\.16\.88\.68',r'C:[/\\]+Users[/\\]+seer']
for p in files:
    text=p.read_text(encoding='utf-8-sig')
    assert not any(re.search(pattern,text) for pattern in patterns),'Unexpected private value in '+str(p.relative_to(repo))
print(json.dumps({'status':'PASS','saved_masks':len(snapshot['shapes']),'verified_operations':len(operations),'images':images,'relative_links_checked':links,'secret_and_private_path_scan':'PASS'},ensure_ascii=False))

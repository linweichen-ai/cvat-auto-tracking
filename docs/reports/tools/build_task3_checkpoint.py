"""Rebuild the published checkpoint from private local evidence; no network.

Usage: python docs/reports/tools/build_task3_checkpoint.py --evidence-root PATH
Only the explicitly listed verified batches and dataset images are exported.
"""
import argparse
import collections
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from cvat_sdk.masks import decode_mask

RUNS = [
    '20261026T072754823638Z', '20261026T073358363855Z',
    '20261026T074211894374Z', '20261026T074603871379Z',
    '20261026T075837675535Z', '20261026T075945765786Z',
    '20261026T080445449754Z', '20261026T080613417695Z',
    '20261026T081038468217Z', '20261026T081201987701Z',
    '20261026T083757679686Z',
    '20261026T084750762710Z',
]
COLORS = [(255,90,90),(50,220,120),(250,215,55),(90,160,255),(230,80,225),(30,220,205),(255,160,50),(160,120,250)]

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def font(size):
    for name in ('arial.ttf','DejaVuSans.ttf'):
        try: return ImageFont.truetype(name, size)
        except OSError: pass
    return ImageFont.load_default(size=size)

def overlay(raw, shapes):
    rgb = np.asarray(raw.convert('RGB')).copy()
    for n,s in enumerate(shapes):
        mask = decode_mask(s['points'], image_width=raw.width, image_height=raw.height).astype(bool)
        color = np.array(COLORS[n % len(COLORS)])
        rgb[mask] = (rgb[mask]*.65+color*.35).astype(np.uint8)
        edge = mask & ~cv2.erode(mask.astype(np.uint8), np.ones((3,3),np.uint8)).astype(bool)
        rgb[edge] = color
    return Image.fromarray(rgb)

def panel(images, labels, title, dest):
    width = sum(im.width for im in images)
    canvas = Image.new('RGB',(width,max(im.height for im in images)+65),(20,26,35))
    draw = ImageDraw.Draw(canvas)
    draw.text((12,8),title,fill='white',font=font(20))
    x=0
    for im,caption in zip(images,labels):
        draw.text((x+10,39),caption,fill=(230,235,240),font=font(15))
        canvas.paste(im,(x,65)); x+=im.width
    canvas.save(dest, quality=92)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--evidence-root',type=Path,required=True)
    root=ap.parse_args().evidence_root.resolve()
    repo=Path(__file__).resolve().parents[3]
    dest=repo/'results/reviewed-workflow/2026-10-26'
    dest.mkdir(parents=True,exist_ok=True)
    original=root/'baseline/live_20261026T070739278666Z'
    initial=read(original/'annotations_after.json')
    final_path=sorted((root/f'incremental_runs/plan_{RUNS[-1]}').glob('step_*/after.json'))[-1]
    final=read(final_path)
    ledger=[]; operations=[]
    for batch,stamp in enumerate(RUNS,1):
        run=root/f'incremental_runs/plan_{stamp}'
        results=read(run/'results.json'); plan=read(run/'plan.json')
        verified=[x for x in results if x.get('status')=='READBACK_VERIFIED']
        ledger.append({'batch':batch,'run_id':f'plan_{stamp}','verified_operations':len(verified),
                       'frames':sorted({x['frame'] for x in verified}),
                       'actions':dict(collections.Counter(x['action'] for x in verified)),
                       'results':results,'candidate_sha256':plan['candidate_sha256'],
                       'annotations_before_sha256':sha(run/'annotations_before.json'),
                       'annotations_after_sha256':sha(sorted(run.glob('step_*/after.json'))[-1])})
        for op in plan['eligible']:
            if any(r.get('status')=='READBACK_VERIFIED' and r['frame']==op['frame'] and r['action']==op['action'] and (r.get('shape_id') in (None,op['shape'].get('id'))) for r in results):
                operations.append({'batch':batch,'frame':op['frame'],'action':op['action'],'shape':op['shape'],
                                   'expected_frame_sha256':op['expected_frame_sha256']})
    summary={'scope':{'task_name':'Task3','task_id':430,'job_id':423,'frames':150},
             'checkpoint_utc':datetime.fromtimestamp(final_path.stat().st_mtime, timezone.utc).isoformat(),'checkpoint_description':f'Final readback of batch {len(RUNS)}; timestamp is evidence file modification time',
             'saved_shapes':len(final['shapes']),'annotated_frames':len({s['frame'] for s in final['shapes']}),
             'label_counts':dict(collections.Counter(str(s['label_id']) for s in final['shapes'])),
             'source_counts':dict(collections.Counter(s['source'] for s in final['shapes'])),
             'verified_operations':sum(x['verified_operations'] for x in ledger),
             'verified_actions':dict(sum((collections.Counter(x['actions']) for x in ledger),collections.Counter())),
             'complete':False,'frames_certified_complete':0,
             'caution':'Saved counts are not recall, accuracy, or a completion metric. Baseline/manual masks have not all passed independent review.',
             'released_manual_edit_frames':[17,19,21,23],
             'final_readback_file_sha256':sha(final_path)}
    write(dest/'checkpoint.json',summary)
    write(dest/'operation-ledger.json',ledger)
    write(dest/'reviewed-operations.json',operations)
    write(dest/'saved-annotations-snapshot.json',final)
    write(dest/'sequence-manifest.json',read(root/'sequence_audit/sequence_manifest.json'))
    image_bindings=[]
    for frame in (75,100,104,106,108,124):
        path=original/f'frame_api_{frame:03d}.jpg'; raw=Image.open(path).convert('RGB')
        before=[s for s in initial['shapes'] if s['frame']==frame]
        after=[s for s in final['shapes'] if s['frame']==frame]
        panel([raw,overlay(raw,before),overlay(raw,after)],['API original',f'Earlier snapshot: {len(before)} masks',f'Checkpoint: {len(after)} masks; ongoing'],
              f'Task3 / frame {frame} / saved masks, not frame completion',dest/f'frame-{frame:03d}-progress.jpg')
        image_bindings.append({'frame':frame,'api_image_sha256':sha(path),'source_frame_name':read(original/'job_data_meta.json')['frames'][frame]['name']})
    for frame in (106,108):
        src=root/f'candidates_wood_refined106108/frame_{frame}_zoom.png'
        im=Image.open(src).convert('RGB'); im.thumbnail((1120,1120))
        panel([im],['6 physical pallets; lowest perspective foot belongs to bottom pallet'],
              f'Independent review: wood-stack frame {frame}',dest/f'wood-stack-{frame}.jpg')
    # Explicit rejection marker: this picture must never be mistaken for an accepted annotation.
    src=root/'doubao_second_review_1600/frame_016_comparison.jpg'
    im=Image.open(src).convert('RGB'); im.thumbnail((1280,640))
    panel([im],['Rejected: coarse rectangular wood masks include box/floor and omit beams'],
          'REJECTED CANDIDATE / NOT WRITTEN / frame 16',dest/'rejected-coarse-boxes.jpg')
    tracks=read(root/'tracking_candidates/seed104_to106_108_v1/summary.json')
    for entry in tracks['results']: entry.pop('source_image',None)
    write(dest/'tracking-104-108-evidence.json',tracks)
    write(dest/'image-bindings.json',image_bindings)
    files=[p for p in dest.rglob('*') if p.is_file() and p.name!='asset-manifest.json']
    write(dest/'asset-manifest.json',[{'path':p.relative_to(repo).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(files)])
    print(json.dumps(summary,ensure_ascii=False))
    print('BATCHES '+json.dumps([{'batch':x['batch'],'count':x['verified_operations'],'frames':x['frames'],'actions':x['actions']} for x in ledger]))

if __name__=='__main__': main()

"""Offline keyframe propagation benchmark. No credentials, network, or CVAT writes.

Reference masks supply only designated seed frames. Other reference masks are
used after prediction for evaluation, never to choose, repair, or stop a track.
This measures agreement with a reviewed snapshot, not independent ground truth.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from cvat_sdk.masks import decode_mask, encode_mask
from PIL import Image


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def box(mask):
    y, x = np.where(mask)
    return [int(x.min()), int(y.min()), int(x.max()) + 1, int(y.max()) + 1] if len(x) else [0, 0, 0, 0]


def overlap(a, b):
    ax, ay, ar, ab = a['box']; bx, by, br, bb = b['box']
    x, y, r, d = max(ax, bx), max(ay, by), min(ar, br), min(ab, bb)
    intersection = int((a['mask'][y:d, x:r] & b['mask'][y:d, x:r]).sum()) if r > x and d > y else 0
    return intersection / max(1, a['area'] + b['area'] - intersection)


def evaluate(pred, refs, threshold=.85):
    pairs = sorted(((overlap(a, b), i, j) for i, a in enumerate(pred) for j, b in enumerate(refs)
                    if a['label'] == b['label']), reverse=True)
    used_p, used_r, matched = set(), set(), []
    for iou, i, j in pairs:
        if iou < threshold: break
        if i not in used_p and j not in used_r:
            used_p.add(i); used_r.add(j); matched.append({'prediction': i, 'reference_id': refs[j]['id'], 'iou': round(iou, 5)})
    labels = sorted({x['label'] for x in pred + refs})
    return {'predictions': len(pred), 'references': len(refs), 'matched_iou85': len(matched),
            'gated_predictions': sum(x.get('gate', False) for x in pred),
            'gated_matched_iou85': sum(pred[x['prediction']].get('gate', False) for x in matched),
            'per_label': {str(label): {'predictions': sum(x['label'] == label for x in pred),
                                      'references': sum(x['label'] == label for x in refs),
                                      'matched_iou85': sum(pred[x['prediction']]['label'] == label for x in matched)} for label in labels},
            'matches': matched}


def record(mask, label, identity, **extra):
    return {'mask': mask, 'box': box(mask), 'area': int(mask.sum()), 'label': label, 'id': identity, **extra}


def overlay(rgb, shapes):
    # Fixed colors are visualization labels only; they never enter inference.
    colors = {201:(60,200,90),202:(250,150,40),203:(25,200,210),204:(245,215,55),205:(230,80,175),206:(165,95,230),207:(60,125,250)}
    result=rgb.copy()
    for s in shapes:
        color=colors.get(s['label'],(170,170,170)); mask=s['mask']
        result[mask] = (.65*result[mask]+.35*np.array(color)).astype(np.uint8)
        contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(result,contours,-1,color,1)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--annotations',type=Path,required=True)
    parser.add_argument('--sequence',type=Path,required=True)
    parser.add_argument('--frames',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--keyframe-intervals',type=int,nargs='+',default=[8,10000])
    parser.add_argument('--frame-pattern',default='frame_api_{frame:03d}.jpg')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    annotations=json.loads(args.annotations.read_text(encoding='utf-8-sig'))
    sequence=json.loads(args.sequence.read_text(encoding='utf-8-sig'))
    if annotations.get('tracks'): raise ValueError('Only image masks are supported')
    ordered=[f for run in sequence['runs'] for f in run['frames']]
    if not ordered or len(ordered)!=len(set(ordered)): raise ValueError('Empty or duplicate sequence frames')
    if any(type(f) is not int or f<0 for f in ordered): raise ValueError('Invalid frame number')
    if not all(n>0 for n in args.keyframe_intervals): raise ValueError('Invalid keyframe interval')
    if any(s['frame'] not in ordered for s in annotations['shapes']): raise ValueError('Reference frames absent from sequence')
    def image_path(frame):
        root=args.frames.resolve();path=(root/args.frame_pattern.format(frame=frame)).resolve()
        if path.parent!=root: raise ValueError('Frame path must stay within image directory')
        return path
    cv2.setNumThreads(2)
    image0=np.asarray(Image.open(image_path(ordered[0])).convert('RGB'))
    h,w=image0.shape[:2]
    refs=defaultdict(list)
    for s in annotations['shapes']:
        if s['type']!='mask': raise ValueError('Only mask references are supported')
        mask=decode_mask(s['points'],image_width=w,image_height=h).astype(bool)
        refs[s['frame']].append(record(mask,s['label_id'],s['id']))
    outputs={n:[] for n in args.keyframe_intervals}; seeds={n:[] for n in args.keyframe_intervals}
    sample_frames={f for run in sequence['runs'] for f in run['frames'][:8]}
    gallery=[]; images=[]; start=time.perf_counter()
    for run in sequence['runs']:
        states={}; previous=None
        for position,frame in enumerate(run['frames']):
            source=image_path(frame); raw=source.read_bytes()
            images.append({'frame':frame,'sha256':hashlib.sha256(raw).hexdigest()})
            rgb=np.asarray(Image.open(source).convert('RGB')); assert rgb.shape==(h,w,3)
            gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
            if previous is not None:
                forward=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(previous,gray,None)
                backward=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(gray,previous,None)
                yy,xx=np.indices((h,w),dtype=np.float32); mx=xx+backward[:,:,0]; my=yy+backward[:,:,1]
                reverse=cv2.remap(forward,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
                consistency=np.linalg.norm(backward+reverse,axis=2)<2.5
            for interval in args.keyframe_intervals:
                if position%interval==0:
                    states[interval]=[dict(s,gate=True) for s in refs[frame]]
                    seeds[interval].append(frame)
                else:
                    next_shapes=[]
                    for s in states[interval]:
                        mask=cv2.remap(s['mask'].astype(np.uint8),mx,my,cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT).astype(bool)
                        area=int(mask.sum())
                        if area<25: continue
                        reliability=float((mask&consistency).sum()/area)
                        ratio=area/max(1,s['area'])
                        gate=bool(s['gate'] and reliability>=.8 and .65<=ratio<=1.5)
                        next_shapes.append(record(mask,s['label'],s['id'],gate=gate,reliability=round(reliability,4)))
                    states[interval]=next_shapes
                    measured=evaluate(next_shapes,refs[frame])
                    outputs[interval].append({'frame':frame,'run':run['run_id'],'steps_since_seed':position%interval,**measured})
                if interval==8 and frame in sample_frames:
                    pred=states[interval]
                    panel=np.concatenate([rgb,overlay(rgb,pred),overlay(rgb,refs[frame])],axis=1)
                    name=f'frame-{frame:03d}.jpg'; Image.fromarray(panel).save(args.out/name,quality=83)
                    gallery.append({'frame':frame,'seed_frame':run['frames'][(position//interval)*interval],
                                    'step':position%interval,'image':name,'evaluation':None if position%interval==0 else outputs[interval][-1]})
                    dump(args.out/f'prediction-{frame:03d}.json',{'frame':frame,'status':'UNREVIEWED_DEMONSTRATION_ONLY',
                         'shapes':[{'label_id':s['label'],'seed_id':s['id'],'type':'mask','points':encode_mask(s['mask'])} for s in pred]})
            previous=gray
        print(json.dumps({'run_complete':run['run_id'],'frames':len(run['frames'])}),flush=True)
    policies={}
    for interval, rows in outputs.items():
        totals={k:sum(x[k] for x in rows) for k in ['predictions','references','matched_iou85','gated_predictions','gated_matched_iou85']}
        per_label={}
        for label in sorted({k for row in rows for k in row['per_label']}):
            per_label[label]={k:sum(row['per_label'].get(label,{}).get(k,0) for row in rows) for k in ['predictions','references','matched_iou85']}
        policies[str(interval)]={'seed_frames':seeds[interval],'seed_mask_count':sum(len(refs[f]) for f in seeds[interval]),
             'prediction_frames':len(rows),**totals,'per_label':per_label,'per_frame':rows}
    summary={'experiment':'Offline DIS propagation from reviewed keyframes; no text detector or SAM2 refinement',
             'reference_sha256':hashlib.sha256(args.annotations.read_bytes()).hexdigest(),
             'sequence_sha256':hashlib.sha256(args.sequence.read_bytes()).hexdigest(),
             'matching':'Same-label greedy one-to-one IoU >= 0.85; no track identity ground truth',
             'reference_used_for_prediction':'Designated keyframe masks only; all other frames evaluation only',
             'elapsed_seconds':round(time.perf_counter()-start,3),'annotation_writes':0,
             'gate':'All propagated steps must have bidirectional-flow reliable fraction >=0.8 and area ratio 0.65..1.5; not a semantic quality guarantee',
             'policies':policies,'gallery':gallery,'images':images}
    dump(args.out/'benchmark.json',summary)
    print(json.dumps({'elapsed_seconds':summary['elapsed_seconds'],'policies':{k:{a:b for a,b in v.items() if a not in ('per_frame','per_label','seed_frames')} for k,v in policies.items()}}))


if __name__=='__main__': main()

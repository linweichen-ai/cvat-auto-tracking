"""Synchronous SAM2 interactor candidates only; no annotation write client."""
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from cvat_sdk.masks import encode_mask, decode_mask
from . import safe_incremental as guard


def write_json(path, value):
    guard.save_json(Path(path),value)


def validate_prompts(value):
    prompts = value.get('prompts') if isinstance(value,dict) else value
    guard.require(isinstance(prompts,list) and bool(prompts),'NONEMPTY_PROMPTS_REQUIRED')
    scope=guard.SCOPE;seen=set()
    for p in prompts:
        guard.require(isinstance(p,dict) and set(p)=={'frame','instance_key','pos_points','neg_points'},'PROMPT_FIELDS_INVALID')
        scope.frame(p['frame'])
        guard.require(p['frame'] not in scope.protected_frames,'HUMAN_PROTECTED_FRAME')
        guard.require(isinstance(p['instance_key'],str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',p['instance_key'])),'INVALID_INSTANCE_KEY')
        identity=(p['frame'],p['instance_key'])
        guard.require(identity not in seen,'DUPLICATE_INSTANCE_KEY');seen.add(identity)
        for name in ('pos_points','neg_points'):
            points=p[name]
            guard.require(isinstance(points,list) and len(points)<=128 and (name!='pos_points' or points),'INVALID_PROMPT_COUNT')
            for point in points:
                guard.require(isinstance(point,list) and len(point)==2 and all(type(x) in (int,float) and math.isfinite(x) for x in point),'FINITE_XY_REQUIRED')
                guard.require(0<=point[0]<scope.width and 0<=point[1]<scope.height,'POINT_OUTSIDE_IMAGE')
    return prompts


def download_images(client, baseline):
    """GET original logical frames; bind saved image hashes to CVAT metadata."""
    scope=guard.SCOPE
    scope.images_dir.mkdir(parents=True,exist_ok=True)
    manifest_path=scope.images_dir/'image_manifest.json'
    guard.require(not manifest_path.exists(),'IMAGE_MANIFEST_ALREADY_EXISTS')
    # Refuse mixed partial directories: rerun into a fresh directory after failure.
    guard.require(all(not scope.image_path(f).exists() for f in range(scope.frame_count)),'SOURCE_IMAGE_ALREADY_EXISTS')
    rows=[]
    for frame in range(scope.frame_count):
        raw=client.frame(frame)
        with Image.open(io.BytesIO(raw)) as image:
            guard.require(image.size==(scope.width,scope.height),'SOURCE_DIMENSIONS_CHANGED')
        path=scope.image_path(frame)
        with path.open('xb') as out:out.write(raw)
        rows.append({'frame':frame,'source_name':baseline['frames'][frame]['name'],'file':path.name,'sha256':hashlib.sha256(raw).hexdigest()})
    write_json(manifest_path,{'base_url':scope.base_url,'task_id':scope.task_id,'job_id':scope.job_id,'frame_metadata_sha256':baseline['metadata_sha256'],'source':'CVAT original frame GET, logical index verified against zero-based job metadata','frames':rows})


def load_sources(baseline, frames):
    scope=guard.SCOPE
    manifest=guard.read_json(scope.images_dir/'image_manifest.json')
    guard.require(manifest.get('base_url')==scope.base_url and manifest.get('task_id')==scope.task_id and manifest.get('job_id')==scope.job_id,'SOURCE_MANIFEST_SCOPE_MISMATCH')
    guard.require(manifest.get('frame_metadata_sha256')==baseline['metadata_sha256'],'SOURCE_METADATA_MISMATCH')
    rows={x['frame']:x for x in manifest['frames']}
    guard.require(len(rows)==scope.frame_count and set(rows)==set(range(scope.frame_count)),'SOURCE_MANIFEST_POPULATION_MISMATCH')
    sources={}
    for frame in frames:
        path=scope.image_path(frame);raw=path.read_bytes();record=rows[frame]
        guard.require(record['file']==path.name and record['source_name']==baseline['frames'][frame]['name'] and record['sha256']==hashlib.sha256(raw).hexdigest(),'SOURCE_IMAGE_HASH_OR_NAME_MISMATCH')
        rgb=np.asarray(Image.open(path).convert('RGB'))
        guard.require(rgb.shape==(scope.height,scope.width,3),'SOURCE_IMAGE_DIMENSIONS_CHANGED')
        sources[frame]=(path,record['sha256'],rgb,record['source_name'])
    return sources


def result_masks(result):
    scope=guard.SCOPE
    guard.require(isinstance(result,dict),'RESULT_OBJECT_REQUIRED')
    if 'mask' in result:
        values=np.asarray(result['mask'])
        guard.require(values.shape==(scope.height,scope.width) and np.isin(values,[0,1,False,True,255]).all(),'INVALID_RETURNED_MASK')
        masks=[values>0]
    elif isinstance(result.get('shapes'),list):
        masks=[]
        for shape in result['shapes']:
            guard.require(shape.get('type','mask')=='mask','UNEXPECTED_RETURNED_SHAPE')
            masks.append(decode_mask(guard.validate_mask(shape['points'],scope.width,scope.height),image_width=scope.width,image_height=scope.height))
    else:
        raise guard.GuardError('MASK_RESULT_MISSING')
    guard.require(bool(masks) and all(m.any() for m in masks),'EMPTY_MASK_RESULT')
    return masks


def visuals(folder,rgb,mask,prompt,*,method='SAM2 candidate',index=0):
    height,width=mask.shape
    vis=rgb.astype(float).copy();vis[mask]=vis[mask]*.55+np.array([255,60,160])*.45
    edge=mask&~cv2.erode(mask.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool);vis[edge]=[255,45,155]
    original=Image.fromarray(rgb);overlay=Image.fromarray(vis.astype(np.uint8))
    overlay.save(folder/f'overlay_{index:02d}.png');Image.fromarray(mask.astype(np.uint8)*255).save(folder/f'mask_{index:02d}.png')
    ys,xs=np.where(mask);box=(max(0,int(xs.min())-12),max(0,int(ys.min())-12),min(width,int(xs.max())+13),min(height,int(ys.max())+13))
    a=original.crop(box);b=overlay.crop(box);scale=min(6,max(1,1100//a.width));w,h=a.width*scale,a.height*scale
    detail=Image.new('RGB',(max(w,600),h*2+60),(20,24,30));draw=ImageDraw.Draw(detail);font=ImageFont.load_default(size=15)
    draw.text((8,4),f"Frame {prompt['frame']} / {prompt['instance_key']} / {method}",font=font,fill='yellow')
    draw.text((8,28),'Original above; candidate below. Independent review required.',font=font,fill='white')
    detail.paste(a.resize((w,h),Image.Resampling.NEAREST),(0,60));detail.paste(b.resize((w,h),Image.Resampling.NEAREST),(0,60+h));detail.save(folder/f'detail_{index:02d}.png')
    drawn=original.copy();draw=ImageDraw.Draw(drawn)
    for name,color in [('pos_points','lime'),('neg_points','red')]:
        for x,y in prompt[name]:draw.ellipse((x-3,y-3,x+3,y+3),outline=color,width=2)
    drawn.save(folder/'input_prompts.png')


def shape(frame,mask):
    scope=guard.SCOPE;points=encode_mask(mask)
    guard.validate_mask(points,scope.width,scope.height)
    guard.require(np.array_equal(mask,decode_mask(points,image_width=scope.width,image_height=scope.height)),'RLE_ROUNDTRIP_FAILED')
    return {'type':'mask','frame':frame,'label_id':guard.LABEL,'source':'auto','occluded':False,'outside':False,'z_order':0,'rotation':0.,'attributes':[],'points':points}


def annotation_audit(before,after):
    return {'remote_annotation_write_requests':0,'async_annotation_requests':0,'annotation_canonical_identical':guard.canonical(before)==guard.canonical(after),'before_sha256':guard.sha(guard.canonical(before)),'after_sha256':guard.sha(guard.canonical(after)),'observed_changed_frames':[f for f in range(guard.FRAME_COUNT) if guard.frame_hash(before,f)!=guard.frame_hash(after,f)],'change_interpretation':'External edits may occur. This command sends no annotation writes.'}


def run(prompts,baseline,output):
    prompts=validate_prompts(prompts);scope=guard.SCOPE
    output=scope.output(output);sources=load_sources(baseline,{p['frame'] for p in prompts})
    output.mkdir(parents=True,exist_ok=False);write_json(output/'prompts.json',{'prompts':prompts})
    client=guard.Client(allow_inference=True)
    live_meta=client.metadata()['job_data_meta.json']
    guard.require(guard.canonical(live_meta)==guard.canonical(guard.read_json(Path(baseline['directory'])/'job_data_meta.json')),'LIVE_FRAME_METADATA_CHANGED')
    client.check_interactor()
    before=client.annotations();write_json(output/'annotations_before.json',before)
    records=[];started=time.perf_counter()
    try:
        # Deliberately sequential: one session, bounded load, no automatic retry.
        for prompt in prompts:
            folder=output/f"frame_{prompt['frame']:06d}_{prompt['instance_key']}";folder.mkdir()
            write_json(folder/'prompt.json',prompt)
            body,result=client.infer(prompt);write_json(folder/'request.json',body)
            with gzip.open(folder/'response.json.gz','wb') as stream:stream.write(guard.canonical(result))
            source,image_hash,rgb,source_name=sources[prompt['frame']];masks=result_masks(result)
            for index,mask in enumerate(masks):visuals(folder,rgb,mask,prompt,index=index)
            candidate={'task_id':scope.task_id,'job_id':scope.job_id,'label_id':guard.LABEL,'function':scope.function_id,'prompt':prompt,'source_image_sha256':image_hash,'source_frame_name':source_name,'frame_metadata_sha256':baseline['metadata_sha256'],'status':'PENDING_AUTHOR_AND_INDEPENDENT_REVIEW','shape_candidates':[shape(prompt['frame'],m) for m in masks]}
            write_json(folder/'candidates.json',candidate)
            records.append({'frame':prompt['frame'],'instance_key':prompt['instance_key'],'candidate_file':str(folder/'candidates.json'),'areas_px':[int(m.sum()) for m in masks]})
    finally:
        after=client.annotations();write_json(output/'annotations_after.json',after)
        write_json(output/'summary.json',{**annotation_audit(before,after),'elapsed_seconds':round(time.perf_counter()-started,3),'results':records})
    return records

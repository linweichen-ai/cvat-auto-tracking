"""Local flow transports prompts; deployed SAM2 refines each frame.

This is not SAM2 video-memory tracking. Metrics cannot prove instance identity.
Every candidate still needs author and independent visual review.
"""
import gzip
import hashlib
import json
from pathlib import Path
import time
import cv2
import numpy as np
from PIL import Image
from . import safe_incremental as guard
from .inference import load_sources, result_masks, visuals, shape, write_json, annotation_audit
from .sequence import build, planned_frames

def warp_seed(previous,current,mask):
    a=cv2.cvtColor(previous,cv2.COLOR_RGB2GRAY);b=cv2.cvtColor(current,cv2.COLOR_RGB2GRAY)
    forward=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(a,b,None)
    backward=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(b,a,None)
    yy,xx=np.indices(mask.shape,dtype=np.float32);mx=xx+backward[:,:,0];my=yy+backward[:,:,1]
    warped=cv2.remap(mask.astype(np.uint8),mx,my,cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT).astype(bool)
    back_forward=cv2.remap(forward,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    consistency=np.linalg.norm(backward+back_forward,axis=2)
    reliable=warped&(consistency<2.5)
    metrics={'warped_area_px':int(warped.sum()),'seed_area_px':int(mask.sum()),
      'reliable_fraction':float(reliable.sum()/max(1,warped.sum())),
      'median_motion_px':float(np.median(np.linalg.norm(forward[mask],axis=1))) if mask.any() else 0}
    return warped,reliable,metrics

def make_prompt(frame,key,warped,reliable):
    core=cv2.erode(reliable.astype(np.uint8),np.ones((3,3),np.uint8))
    if core.sum()<15:raise ValueError('INSUFFICIENT_RELIABLE_MASK_INTERIOR')
    dist=cv2.distanceTransform(core,cv2.DIST_L2,5);positive=[]
    radius=max(8,int(np.sqrt(warped.sum())/5))
    for _ in range(5):
        _,value,_,point=cv2.minMaxLoc(dist)
        if value<1:break
        positive.append([int(point[0]),int(point[1])]);cv2.circle(dist,point,radius,0,-1)
    # Only the exterior annulus supplies negatives: never pallet's inner holes.
    contours,_=cv2.findContours(warped.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    silhouette=np.zeros_like(core);cv2.drawContours(silhouette,contours,-1,1,-1)
    ring=(cv2.dilate(silhouette,np.ones((31,31),np.uint8))>0)&~(cv2.dilate(silhouette,np.ones((11,11),np.uint8))>0)
    ys,xs=np.where(warped);cx=(xs.min()+xs.max())/2;cy=(ys.min()+ys.max())/2
    ry,rx=np.where(ring);negative=[]
    for angle in np.arange(8)*np.pi/4:
        target=np.array([cx+np.cos(angle)*((xs.max()-xs.min())/2+10),cy+np.sin(angle)*((ys.max()-ys.min())/2+10)])
        if len(rx):
            selected=int(np.argmin((rx-target[0])**2+(ry-target[1])**2));point=[int(rx[selected]),int(ry[selected])]
            if point not in negative:negative.append(point)
    return {'frame':frame,'instance_key':key,'pos_points':positive,'neg_points':negative}


def preflight(seed_frame,seed_mask,seed_review,steps,key,baseline):
    """Validate a seed and its complete frame plan without any remote calls."""
    scope=guard.SCOPE;seed_mask=Path(seed_mask)
    review=guard.read_json(seed_review)
    guard.require(review.get('status')=='approved' and review.get('author') and review.get('reviewer') and review['author']!=review['reviewer'],'REVIEWED_SEED_REQUIRED')
    guard.require(review.get('mask_sha256')==hashlib.sha256(seed_mask.read_bytes()).hexdigest(),'SEED_REVIEW_HASH_MISMATCH')
    guard.require(review.get('task_id')==scope.task_id and review.get('job_id')==scope.job_id and review.get('frame')==seed_frame and review.get('label_id')==guard.LABEL,'SEED_REVIEW_SCOPE_MISMATCH')
    meta=guard.read_json(Path(baseline['directory'])/'job_data_meta.json');manifest=build(scope,meta)
    run_id,targets=planned_frames(scope,seed_frame,steps,manifest)
    sources=load_sources(baseline,[seed_frame,*targets])
    guard.require(review.get('source_image_sha256')==sources[seed_frame][1],'SEED_SOURCE_IMAGE_MISMATCH')
    mask=np.asarray(Image.open(seed_mask).convert('L'))>0
    guard.require(mask.shape==(scope.height,scope.width) and mask.any(),'INVALID_SEED_MASK')
    from .inference import validate_prompts
    validate_prompts([{'frame':targets[0],'instance_key':key,'pos_points':[[0,0]],'neg_points':[]}])
    return {'review':review,'manifest':manifest,'run_id':run_id,'targets':targets,
            'sources':sources,'mask':mask,'meta':meta}


def run(seed_frame,seed_mask,seed_review,steps,key,baseline,output):
    scope=guard.SCOPE
    prepared=preflight(seed_frame,seed_mask,seed_review,steps,key,baseline)
    review=prepared['review'];manifest=prepared['manifest'];run_id=prepared['run_id']
    targets=prepared['targets'];sources=prepared['sources'];mask=prepared['mask'];meta=prepared['meta']
    output=scope.output(output);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'sequence_manifest.json',manifest)
    write_json(output/'seed_review.json',review)
    write_json(output/'config.json',{'seed_frame':seed_frame,'seed_mask_sha256':review['mask_sha256'],'planned_targets':targets,'run_id':run_id,'method':'DIS flow -> positive/negative prompts -> per-frame SAM2 interactor. No video memory.','all_outputs_require_independent_review':True})
    client=guard.Client(allow_inference=True)
    live_meta=client.metadata()['job_data_meta.json']
    guard.require(guard.canonical(live_meta)==guard.canonical(meta),'LIVE_FRAME_METADATA_CHANGED')
    client.check_interactor()
    before=client.annotations();write_json(output/'annotations_before.json',before)
    previous=sources[seed_frame][2];previous_frame=seed_frame;records=[];started=time.perf_counter()
    try:
        for target in targets:
            folder=output/f'frame_{target:06d}';folder.mkdir()
            source,image_hash,current,source_name=sources[target]
            warped,reliable,metrics=warp_seed(previous,current,mask)
            Image.fromarray(warped.astype(np.uint8)*255).save(folder/'flow_warp_mask.png')
            Image.fromarray(reliable.astype(np.uint8)*255).save(folder/'flow_reliable_mask.png')
            if metrics['warped_area_px']<25 or metrics['reliable_fraction']<.5 or metrics['median_motion_px']>80:
                records.append({'frame':target,'status':'STOPPED_FLOW_UNRELIABLE','metrics':metrics});break
            prompt=make_prompt(target,key,warped,reliable);write_json(folder/'prompt.json',prompt)
            body,result=client.infer(prompt);write_json(folder/'request.json',body)
            with gzip.open(folder/'response.json.gz','wb') as stream:stream.write(guard.canonical(result))
            masks=result_masks(result);chosen=max(masks,key=lambda m:float((m&warped).sum()/max(1,(m|warped).sum())))
            metrics['sam2_vs_warp_iou']=float((chosen&warped).sum()/max(1,(chosen|warped).sum()))
            metrics['sam2_vs_warp_area_ratio']=float(chosen.sum()/max(1,warped.sum()))
            drift=metrics['sam2_vs_warp_iou']<.4 or not .4<=metrics['sam2_vs_warp_area_ratio']<=2
            visuals(folder,current,chosen,prompt,method='Flow prompts + SAM2 candidate')
            record={'task_id':scope.task_id,'job_id':scope.job_id,'label_id':guard.LABEL,'prompt':prompt,'previous_frame':previous_frame,'source_image_sha256':image_hash,'source_frame_name':source_name,'frame_metadata_sha256':baseline['metadata_sha256'],'metrics':metrics,'status':'REJECTED_NUMERICAL_DRIFT' if drift else 'PENDING_AUTHOR_AND_INDEPENDENT_REVIEW','shape_candidates':[shape(target,chosen)]}
            write_json(folder/'candidates.json',record)
            records.append({'frame':target,'status':record['status'],'metrics':metrics,'candidate_file':str(folder/'candidates.json')})
            if drift:break
            previous,current=current,None;previous_frame=target;mask=chosen
    finally:
        after=client.annotations();write_json(output/'annotations_after.json',after)
        write_json(output/'summary.json',{**annotation_audit(before,after),'elapsed_seconds':round(time.perf_counter()-started,3),'results':records})
    return records

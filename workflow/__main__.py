"""CLI: python -m workflow --config CONFIG --label NAME COMMAND ..."""
import argparse
import json
from pathlib import Path
from urllib import error
from .config import Scope, ScopeError
from . import safe_incremental as guard


def bind_reviewed(index_path, baseline, output):
    """Bind reviewed *create* candidates to a stable baseline, offline only.

    Updates/deletes require explicitly authored changes with exact shape IDs.
    Review names are an audit trail, not an authentication/authorization system.
    """
    from .inference import load_sources
    index_path=Path(index_path).resolve();index=guard.read_json(index_path)
    guard.require(index.get('status')=='approved' and index.get('author') and index.get('reviewer') and index['author']!=index['reviewer'],'TWO_DISTINCT_REVIEWERS_REQUIRED')
    guard.require(isinstance(index.get('entries'),list) and bool(index['entries']),'REVIEW_ENTRIES_REQUIRED')
    changes=[];evidence=[]
    for item in index['entries']:
        path=(index_path.parent/item['candidate_file']).resolve();raw=path.read_bytes()
        guard.require(guard.sha(raw)==item.get('candidate_sha256'),'REVIEWED_CANDIDATE_FILE_CHANGED')
        data=json.loads(raw)
        guard.require(data.get('task_id')==guard.TASK and data.get('job_id')==guard.JOB and data.get('label_id')==guard.LABEL,'CANDIDATE_SCOPE_MISMATCH')
        guard.require(data.get('status')=='PENDING_AUTHOR_AND_INDEPENDENT_REVIEW','REJECTED_OR_UNKNOWN_CANDIDATE_STATUS')
        guard.require(data.get('frame_metadata_sha256')==baseline['metadata_sha256'],'CANDIDATE_METADATA_MISMATCH')
        shapes=data.get('shape_candidates',[])
        guard.require(len(shapes)==1,'EXPLICIT_SINGLE_INSTANCE_SELECTION_REQUIRED')
        shape=shapes[0];frame=shape['frame'];source=load_sources(baseline,[frame])[frame]
        guard.require(shape.get('type')=='mask' and shape.get('label_id')==guard.LABEL,'CANDIDATE_SHAPE_SCOPE_MISMATCH')
        guard.require(data.get('source_image_sha256')==source[1] and data.get('source_frame_name')==source[3],'CANDIDATE_SOURCE_IMAGE_MISMATCH')
        changes.append({'frame':frame,'action':'create','points':shape['points'],'occluded':shape.get('occluded',False)})
        evidence.append({'candidate_file':str(path),'sha256':item['candidate_sha256']})
    candidate={'schema':'cvat_incremental_candidate_v1','task_id':guard.TASK,'job_id':guard.JOB,'label_id':guard.LABEL,'baseline_annotations_sha256':baseline['annotations_sha256'],'frame_metadata_sha256':baseline['metadata_sha256'],'changes':changes,'evidence':evidence}
    guard.build_plan(candidate,baseline)
    candidate['review']={'status':'approved','author':index['author'],'reviewer':index['reviewer'],'candidate_sha256':guard.candidate_digest(candidate)}
    guard.save_json(guard.SCOPE.output(output),candidate)
    return {'status':'OFFLINE_REVIEWED_CREATE_PLAN_BOUND','changes':len(changes),'candidate_sha256':guard.candidate_digest(candidate),'remote_calls':0}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--label',required=True)
    sub=p.add_subparsers(dest='command',required=True)
    snap=sub.add_parser('snapshot',help='Read-only stable annotations + metadata backup')
    snap.add_argument('--out',type=Path,required=True);snap.add_argument('--download-frames',action='store_true')
    sequence=sub.add_parser('sequence',help='Offline conservative sequence audit')
    sequence.add_argument('--baseline',type=Path,required=True);sequence.add_argument('--out',type=Path,required=True)
    infer=sub.add_parser('infer',help='Pure synchronous SAM2; outputs unapproved candidates')
    infer.add_argument('--baseline',type=Path,required=True);infer.add_argument('--prompts',type=Path,required=True);infer.add_argument('--out',type=Path,required=True)
    propagate=sub.add_parser('propagate',help='Same-sequence flow prompts + per-frame SAM2')
    propagate.add_argument('--baseline',type=Path,required=True);propagate.add_argument('--seed-frame',type=int,required=True);propagate.add_argument('--seed-mask',type=Path,required=True);propagate.add_argument('--seed-review',type=Path,required=True);propagate.add_argument('--steps',type=int,default=8);propagate.add_argument('--instance-key',required=True);propagate.add_argument('--out',type=Path,required=True)
    bind=sub.add_parser('bind',help='Offline bind an explicitly double-reviewed create index')
    bind.add_argument('--baseline',type=Path,required=True);bind.add_argument('--reviewed-index',type=Path,required=True);bind.add_argument('--out',type=Path,required=True)
    plan=sub.add_parser('plan',help='Offline dry-run by default; explicit --apply enables PATCH')
    plan.add_argument('--baseline',type=Path,required=True);plan.add_argument('--candidate',type=Path,required=True);plan.add_argument('--current',type=Path);plan.add_argument('--out',type=Path);plan.add_argument('--apply',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv);scope=Scope.load(args.config);guard.configure(scope,args.label)
    if args.command=='snapshot':
        output=scope.output(args.out);output.mkdir(parents=True,exist_ok=False)
        client=guard.Client();manifest=guard.full_snapshot(client,output)
        if args.download_frames:
            from .inference import download_images
            download_images(client,guard.load_baseline(output))
        return {'status':'READONLY_SNAPSHOT_SAVED','directory':str(output),'stable':manifest['annotation_canonical_identical']}
    baseline=guard.load_baseline(args.baseline)
    if args.command=='sequence':
        from .sequence import build
        result=build(scope,guard.read_json(Path(baseline['directory'])/'job_data_meta.json'))
        output=scope.output(args.out);output.parent.mkdir(parents=True,exist_ok=True);guard.save_json(output,result)
        return {'status':'OFFLINE_SEQUENCE_SAVED','runs':result['runs']}
    if args.command=='infer':
        from .inference import run
        return {'status':'UNAPPROVED_CANDIDATES_SAVED','results':run(guard.read_json(args.prompts),baseline,args.out)}
    if args.command=='propagate':
        from .propagation import run
        return {'status':'UNAPPROVED_PROPAGATION_SAVED','results':run(args.seed_frame,args.seed_mask,args.seed_review,args.steps,args.instance_key,baseline,args.out)}
    if args.command=='bind':
        return bind_reviewed(args.reviewed_index,baseline,args.out)
    candidate=guard.read_json(args.candidate)
    guard.require(not(args.apply and args.current),'APPLY_REQUIRES_FRESH_NETWORK_READ')
    plan=guard.build_plan(candidate,baseline,guard.read_json(args.current) if args.current else None)
    if not args.apply:
        return {'status':'OFFLINE_DRYRUN','candidate_sha256':plan['candidate_sha256'],'eligible':[{'frame':x['frame'],'action':x['action'],'shape_id':x['shape'].get('id')} for x in plan['eligible']],'blocked':plan['blocked'],'remote_calls':0}
    guard.ensure_review(candidate)
    output=scope.output(args.out or scope.work_dir/'apply_runs'/guard.timestamp());output.mkdir(parents=True,exist_ok=False)
    lock=scope.work_dir/'incremental_apply.lock';acquired=False;retain=False
    try:
        with lock.open('x',encoding='utf-8') as stream:stream.write(str(output))
        acquired=True
        client=guard.Client(allow_apply=True)
        remote_meta=client.metadata()['job_data_meta.json']
        guard.require(guard.canonical(remote_meta)==guard.canonical(guard.read_json(Path(baseline['directory'])/'job_data_meta.json')),'LIVE_FRAME_METADATA_CHANGED')
        results=guard.apply_candidate(client,candidate,baseline,output)
        return {'status':'APPLIED_WITH_READBACK','results':results,'directory':str(output)}
    except guard.GuardError as exc:
        retain=str(exc)=='PATCH_OUTCOME_REQUIRES_READONLY_RECONCILIATION'
        raise
    finally:
        if acquired and not retain:lock.unlink()


if __name__=='__main__':
    try:
        print(json.dumps(main(),ensure_ascii=False))
    except (guard.GuardError,ScopeError) as exc:
        print(json.dumps({'status':'GUARD_REJECTED','code':str(exc)}));raise SystemExit(2)
    except error.HTTPError as exc:
        print(json.dumps({'status':'HTTP_FAILED','http_status':exc.code}));raise SystemExit(3)
    except Exception as exc:
        # Never echo response bodies, headers, request objects, secrets, or tracebacks.
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__}));raise SystemExit(4)

"""Batch reviewed seeds into unapproved propagation candidates; dry-run by default.

Authentication remains environment-only in the existing client. This module has
no annotation writer and never converts numerical checks into review approval.
"""
import argparse
import json
from pathlib import Path
from .config import Scope, ScopeError
from . import safe_incremental as guard
from . import propagation


def prepare(scope, baseline_path, manifest_path):
    manifest_path=Path(manifest_path).resolve()
    manifest=guard.read_json(manifest_path)
    guard.require(isinstance(manifest,dict) and set(manifest)=={'instances'},'BATCH_MANIFEST_FIELDS_INVALID')
    entries=manifest['instances']
    guard.require(isinstance(entries,list) and 1<=len(entries)<=1000,'BATCH_REQUIRES_1_TO_1000_INSTANCES')
    seen=set();prepared=[]
    for index, entry in enumerate(entries):
        guard.require(isinstance(entry,dict) and set(entry)=={'label','seed_frame','seed_mask','seed_review','steps','instance_key'},'BATCH_ENTRY_FIELDS_INVALID')
        guard.require(isinstance(entry['label'],str) and entry['label'] in scope.labels,'BATCH_LABEL_NOT_CONFIGURED')
        key=entry['instance_key'];guard.require(isinstance(key,str),'BATCH_INSTANCE_KEY_REQUIRED')
        guard.require(key not in seen,'BATCH_INSTANCE_KEY_DUPLICATED');seen.add(key)
        guard.require(all(isinstance(entry[k],str) and entry[k] for k in ('seed_mask','seed_review')),'BATCH_SEED_PATH_REQUIRED')
        guard.configure(scope,entry['label'])
        baseline=guard.load_baseline(baseline_path)
        item=dict(entry,seed_mask=(manifest_path.parent/entry['seed_mask']).resolve(),
                  seed_review=(manifest_path.parent/entry['seed_review']).resolve())
        check=propagation.preflight(item['seed_frame'],item['seed_mask'],item['seed_review'],item['steps'],key,baseline)
        prepared.append({'item':item,'targets':check['targets'],'run_id':check['run_id'],'baseline':baseline})
    return prepared


def execute(scope, prepared, output, *, run=False):
    plan=[{'instance_key':p['item']['instance_key'],'label':p['item']['label'],
           'seed_frame':p['item']['seed_frame'],'target_frames':p['targets'],'run_id':p['run_id']} for p in prepared]
    if not run:
        return {'status':'OFFLINE_BATCH_DRYRUN','remote_calls':0,'annotation_write_requests':0,'instances':plan}
    output=scope.output(output);output.mkdir(parents=True,exist_ok=False)
    receipt={'status':'RUNNING','annotation_write_requests':0,'automatic_review_approval':False,'plan':plan,'results':[]}
    def save():
        temp=output/'batch-summary.tmp'
        temp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temp.replace(output/'batch-summary.json')
    save()
    for index,p in enumerate(prepared):
        entry=p['item'];guard.configure(scope,entry['label'])
        try:
            records=propagation.run(entry['seed_frame'],entry['seed_mask'],entry['seed_review'],entry['steps'],entry['instance_key'],p['baseline'],output/f'{index:04d}-{entry["instance_key"]}')
            stopped=any(x['status']!='PENDING_AUTHOR_AND_INDEPENDENT_REVIEW' for x in records)
            receipt['results'].append({'instance_key':entry['instance_key'],'status':'STOPPED_FOR_QUALITY' if stopped else 'CANDIDATES_REQUIRE_REVIEW','results':records})
            save()
        except Exception as exc:
            # No raw HTTP body, credentials, or exception text in the receipt.
            receipt['status']='STOPPED_ON_ERROR_NO_AUTOMATIC_RETRY'
            receipt['results'].append({'instance_key':entry['instance_key'],'status':'ERROR','error_type':type(exc).__name__})
            save();return receipt
    receipt['status']='FINISHED_WITH_STOPPED_INSTANCES' if any(x['status']=='STOPPED_FOR_QUALITY' for x in receipt['results']) else 'CANDIDATES_READY_FOR_REVIEW'
    save();return receipt


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--out',type=Path)
    p.add_argument('--run',action='store_true',help='Call the configured pure interactor; never save annotations')
    args=p.parse_args(argv);scope=Scope.load(args.config)
    guard.require(not args.run or args.out is not None,'RUN_OUTPUT_REQUIRED')
    prepared=prepare(scope,args.baseline,args.manifest)
    return execute(scope,prepared,args.out,run=args.run)


if __name__=='__main__':
    try:
        result=main();print(json.dumps(result,ensure_ascii=False))
        if result['status']=='STOPPED_ON_ERROR_NO_AUTOMATIC_RETRY':raise SystemExit(4)
    except (guard.GuardError,ScopeError) as exc:
        print(json.dumps({'status':'GUARD_REJECTED','code':str(exc)}));raise SystemExit(2)
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__}));raise SystemExit(4)

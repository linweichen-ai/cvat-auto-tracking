"""Derive conservative capture runs from every source timestamp, never parity."""
import hashlib
import json
from pathlib import Path
import re
from .config import ScopeError


def build(scope, metadata):
    if len(metadata.get('frames',[])) != scope.frame_count or metadata.get('start_frame') != 0 or metadata.get('stop_frame') != scope.frame_count-1 or metadata.get('frame_filter') or metadata.get('deleted_frames'):
        raise ScopeError('UNSUPPORTED_FRAME_MAPPING')
    rows = [{'frame':i,'source_name':x['name'],'protected':i in scope.protected_frames} for i,x in enumerate(metadata['frames'])]
    settings = scope.sequence
    boundaries = []
    if settings['mode'] == 'explicit':
        runs = [list(run) for run in settings['runs']]
        method = 'Explicit user-verified sequence partition; no cross-run propagation.'
    else:
        regex = re.compile(settings['filename_regex'])
        for row in rows:
            match = regex.fullmatch(Path(row['source_name']).name)
            if not match:
                raise ScopeError('UNPARSED_SOURCE_FILENAME')
            row.update(source_index=int(match['index']),timestamp_ns=int(match['timestamp_ns']))
        if len({r['timestamp_ns'] for r in rows}) != scope.frame_count:
            raise ScopeError('DUPLICATE_SOURCE_TIMESTAMP')
        ordered = sorted(rows,key=lambda r:r['timestamp_ns'])
        runs = [[]]
        previous = None
        for row in ordered:
            if previous:
                gap = (row['timestamp_ns']-previous['timestamp_ns'])/1e9
                delta = row['source_index']-previous['source_index']
                if not 0 < gap <= settings['max_gap_seconds'] or delta != settings['source_index_step']:
                    boundaries.append({'from_frame':previous['frame'],'to_frame':row['frame'],'gap_seconds':gap,'source_index_delta':delta,'propagation_allowed':False})
                    runs.append([])
            runs[-1].append(row['frame'])
            previous = row
        method = 'Every source timestamp sorted; split on configured time gap or index step. Physical recording identity is not inferred.'
    result = []
    for i, run in enumerate(runs):
        run_id = f'capture_run_{i+1:02d}'
        for position, frame in enumerate(run):
            rows[frame].update(run_id=run_id,run_position=position)
        result.append({'run_id':run_id,'frames':run,'count':len(run)})
    return {'task_id':scope.task_id,'job_id':scope.job_id,'method':method,'runs':result,'boundaries':boundaries,'frames':rows}


def planned_frames(scope, seed_frame, steps, manifest):
    scope.frame(seed_frame)
    if type(steps) is not int or not 1 <= steps <= 25:
        raise ScopeError('STEPS_MUST_BE_1_TO_25')
    if manifest.get('task_id') != scope.task_id or manifest.get('job_id') != scope.job_id:
        raise ScopeError('SEQUENCE_SCOPE_MISMATCH')
    run = next((r for r in manifest['runs'] if seed_frame in r['frames']),None)
    if run is None:
        raise ScopeError('SEED_NOT_IN_SEQUENCE')
    start = run['frames'].index(seed_frame)+1
    targets=[]
    for frame in run['frames'][start:start+steps]:
        if frame in scope.protected_frames:
            break
        targets.append(frame)
    if not targets:
        raise ScopeError('NO_UNPROTECTED_SUCCESSOR_IN_SAME_RUN')
    return run['run_id'], targets

import copy
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from cvat_sdk.masks import encode_mask,decode_mask
from workflow.config import Scope,ScopeError
from workflow import safe_incremental as guard
from workflow.inference import validate_prompts,result_masks
from workflow.sequence import build,planned_frames
from workflow.tests.test_incremental import baseline,candidate
from workflow.__main__ import main


def config():
    return {'base_url':'https://cvat.example.invalid','task_id':101,'job_id':7,'labels':{'pallet':12},'frame_count':6,'width':4,'height':3,'function_id':'sam2','protected_frames':[], 'work_dir':'work','images_dir':'frames','frame_pattern':'frame_{frame:06d}.jpg','sequence':{'mode':'timestamps','filename_regex':r'frame_(?P<index>\d+)_(?P<timestamp_ns>\d+)\.png','max_gap_seconds':3,'source_index_step':6}}


def meta():
    pairs=[(12,3),(0,1),(0,10),(6,2),(6,11),(18,4)]
    return {'start_frame':0,'stop_frame':5,'frames':[{'width':4,'height':3,'name':f'frame_{i}_{t*1000000000}.png'} for i,t in pairs]}


class ScopeSequenceTests(unittest.TestCase):
    def setUp(self):
        self.scope=Scope.from_dict(config());guard.configure(self.scope,'pallet')

    def test_invalid_scope_and_secret_fields_rejected(self):
        for field,value in [('job_id',True),('width',0),('base_url','https://u:p@example.invalid'),('base_url','https://example.invalid/api?x=1'),('function_id','../requests'),('frame_pattern','../frame_{frame}.jpg'),('protected_frames',[6]),('password','forbidden')]:
            data=config();data[field]=value
            with self.subTest(field=field),self.assertRaises((ScopeError,ValueError)):Scope.from_dict(data)

    def test_frame_source_and_output_directory_guards(self):
        self.assertEqual(self.scope.image_path(3).name,'frame_000003.jpg')
        for f in [-1,6,True]:
            with self.assertRaises(ScopeError):self.scope.image_path(f)
        with self.assertRaises(ScopeError):self.scope.output(self.scope.work_dir.parent/'elsewhere')

    def test_all_timestamps_sorted_no_parity_or_cross_gap_assumption(self):
        manifest=build(self.scope,meta())
        self.assertEqual([r['frames'] for r in manifest['runs']],[[1,3,0,5],[2,4]])
        self.assertEqual(planned_frames(self.scope,3,25,manifest)[1],[0,5])
        with self.assertRaises(ScopeError):planned_frames(self.scope,5,3,manifest)

    def test_bad_frame_mapping_or_duplicate_timestamps_rejected(self):
        for change in ['start','filter','duplicate']:
            m=meta()
            if change=='start':m['start_frame']=1
            elif change=='filter':m['frame_filter']='step=2'
            else:m['frames'][0]['name']=m['frames'][1]['name']
            with self.subTest(change=change),self.assertRaises(ScopeError):build(self.scope,m)

    def test_protected_frame_stops_sequence_and_can_be_explicitly_released(self):
        data=config();data['protected_frames']=[0];scope=Scope.from_dict(data)
        self.assertEqual(planned_frames(scope,1,25,build(scope,meta()))[1],[3])
        with self.assertRaises(ScopeError):planned_frames(scope,3,2,build(scope,meta()))
        data['protected_frames']=[];released=Scope.from_dict(data)
        self.assertEqual(planned_frames(released,3,2,build(released,meta()))[1],[0,5])

    def test_explicit_runs_partition_all_frames(self):
        data=config();data['sequence']={'mode':'explicit','runs':[[1,3,0,5],[2,4]]}
        scope=Scope.from_dict(data)
        self.assertEqual([r['frames'] for r in build(scope,meta())['runs']],[[1,3,0,5],[2,4]])
        for runs in [[[0,1,2]],[[0,1,2,3,4,5,5]],[[0,1,2,3,4,6]]]:
            data['sequence']['runs']=runs
            with self.assertRaises(ScopeError):Scope.from_dict(data)

    def test_protected_frame_rejects_inference_and_write_plan(self):
        data=config();data['protected_frames']=[1];guard.configure(Scope.from_dict(data),'pallet')
        with self.assertRaisesRegex(guard.GuardError,'PROTECTED'):
            validate_prompts([{'frame':1,'instance_key':'p','pos_points':[[1,1]],'neg_points':[]}])
        with self.assertRaisesRegex(guard.GuardError,'PROTECTED'):
            guard.prepare_change({'frame':1,'action':'create','points':[0,1,0,0,0,0]},baseline())

    def test_occluded_create_update_and_delete_contract(self):
        base=baseline()
        self.assertTrue(guard.prepare_change({'frame':1,'action':'create','points':[0,1,0,0,0,0],'occluded':True},base)['shape']['occluded'])
        base['annotations']['shapes'][0]['occluded']=True
        self.assertTrue(guard.prepare_change({'frame':0,'action':'update','shape_id':10,'points':[0,1,0,0,0,0]},base)['shape']['occluded'])
        for change in [{'frame':1,'action':'create','points':[0,1,0,0,0,0],'occluded':1},{'frame':0,'action':'delete','shape_id':10,'occluded':False}]:
            with self.assertRaises(guard.GuardError):guard.prepare_change(change,base)

    def test_same_author_reviewer_not_accepted(self):
        value=candidate([{'frame':0,'action':'delete','shape_id':10}]);value['review']['author']=value['review']['reviewer']
        with self.assertRaisesRegex(guard.GuardError,'DISTINCT'):guard.ensure_review(value)

    def test_prompt_bounds_and_mask_dimensions(self):
        p={'frame':1,'instance_key':'p','pos_points':[[3,2]],'neg_points':[]};validate_prompts([p])
        for point in [[4,0],[0,3],[float('nan'),0],[True,0]]:
            bad=copy.deepcopy(p);bad['pos_points']=[point]
            with self.assertRaises(guard.GuardError):validate_prompts([bad])
        with self.assertRaises(guard.GuardError):result_masks({'mask':[[1]]})

    def test_rle_against_official_sdk_many_masks(self):
        random=np.random.default_rng(7)
        for _ in range(100):
            mask=random.integers(0,2,size=(3,4),dtype=np.uint8).astype(bool)
            if not mask.any():continue
            points=encode_mask(mask);guard.validate_mask(points,4,3)
            self.assertTrue(np.array_equal(mask,decode_mask(points,image_width=4,image_height=3)))

    def test_transport_refuses_async_other_job_and_full_replacement(self):
        client=object.__new__(guard.Client);client.allow_apply=False;client.allow_inference=True
        for method,path in [('POST','/api/lambda/requests'),('POST','/api/lambda/functions/wrong'),('GET','/api/jobs/8/annotations'),('PUT',guard.ANNOTATIONS),('PATCH',guard.ANNOTATIONS+'?action=create')]:
            with self.subTest(path=path),self.assertRaises(guard.GuardError):client._request(method,path,{})

    def test_client_cannot_reuse_authentication_after_scope_switch(self):
        client=object.__new__(guard.Client);client.allow_apply=False;client.allow_inference=False;client.bound_scope=self.scope
        new=config();new['base_url']='https://different.example.invalid';guard.configure(Scope.from_dict(new),'pallet')
        with self.assertRaisesRegex(guard.GuardError,'SCOPE_CHANGED'):
            client._request('GET',guard.ANNOTATIONS)

    def test_snapshot_file_hash_origin_and_cli_offline_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);settings=config();settings['work_dir']=str(root);settings['images_dir']=str(root/'frames')
            scope=Scope.from_dict(settings);guard.configure(scope,'pallet');folder=root/'baseline';folder.mkdir()
            class ReadOnlyFake:
                def annotations(self):return copy.deepcopy(baseline()['annotations'])
                def metadata(self):return {'job.json':{'id':7,'task_id':101,'start_frame':0,'stop_frame':5},'task.json':{'id':101},'job_data_meta.json':meta(),'labels_page_1.json':{'results':[{'id':12,'name':'pallet'}]}}
            guard.full_snapshot(ReadOnlyFake(),folder);base=guard.load_baseline(folder)
            proposal=candidate([{'frame':1,'action':'create','points':[0,1,2,0,2,0]}]);proposal['baseline_annotations_sha256']=base['annotations_sha256'];proposal['frame_metadata_sha256']=base['metadata_sha256']
            config_path=root/'config.json';config_path.write_text(json.dumps(settings));candidate_path=root/'candidate.json';candidate_path.write_text(json.dumps(proposal))
            with patch.object(guard,'Client',side_effect=AssertionError('Offline plan must never authenticate')):
                result=main(['--config',str(config_path),'--label','pallet','plan','--baseline',str(folder),'--candidate',str(candidate_path)])
            self.assertEqual(result['remote_calls'],0);self.assertEqual(result['status'],'OFFLINE_DRYRUN')
            original=(folder/'manifest.json').read_text();manifest=json.loads(original);manifest['base_url']='https://other.example.invalid';(folder/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(guard.GuardError,'ORIGIN'):guard.load_baseline(folder)
            (folder/'manifest.json').write_text(original);(folder/'job_data_meta.json').write_text('{}')
            with self.assertRaisesRegex(guard.GuardError,'FILE_HASH'):guard.load_baseline(folder)


if __name__=='__main__':unittest.main()

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from cvat_sdk.masks import encode_mask
from workflow import batch, benchmark_propagation as benchmark
from workflow.config import Scope
from workflow import safe_incremental as guard


def scope(root):
    return Scope.from_dict({'base_url':'https://cvat.example.invalid','task_id':101,'job_id':7,
        'labels':{'pallet':12},'frame_count':3,'width':32,'height':32,'function_id':'sam2',
        'protected_frames':[],'work_dir':'work','images_dir':'frames','frame_pattern':'frame_{frame:03d}.jpg',
        'sequence':{'mode':'explicit','runs':[[0,1,2]]}},root)


class BatchBenchmarkTests(unittest.TestCase):
    def test_label_and_duplicate_predictions_cannot_inflate_matching(self):
        mask=np.ones((4,4),dtype=bool)
        p=[benchmark.record(mask,12,1),benchmark.record(mask,12,2),benchmark.record(mask,13,3)]
        r=[benchmark.record(mask,12,4)]
        self.assertEqual(benchmark.evaluate(p,r)['matched_iou85'],1)
        self.assertEqual(benchmark.evaluate([p[2]],r)['matched_iou85'],0)

    def test_target_only_object_is_not_leaked_into_prediction(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);shapes=[]
            for f in range(3):
                Image.fromarray(np.zeros((32,32,3),dtype=np.uint8)).save(root/f'frame_api_{f:03d}.jpg')
                a=np.zeros((32,32),dtype=bool);a[4:12,4:12]=True
                shapes.append({'id':f*2+1,'type':'mask','frame':f,'label_id':12,'points':encode_mask(a)})
                if f:
                    b=np.zeros_like(a);b[20:28,20:28]=True
                    shapes.append({'id':f*2+2,'type':'mask','frame':f,'label_id':13,'points':encode_mask(b)})
            (root/'annotations.json').write_text(json.dumps({'shapes':shapes,'tracks':[]}))
            (root/'sequence.json').write_text(json.dumps({'runs':[{'run_id':'one','frames':[0,1,2]}]}))
            argv=['benchmark','--annotations',str(root/'annotations.json'),'--sequence',str(root/'sequence.json'),
                  '--frames',str(root),'--out',str(root/'out'),'--keyframe-intervals','8']
            with patch('sys.argv',argv),contextlib.redirect_stdout(io.StringIO()):benchmark.main()
            result=json.loads((root/'out/benchmark.json').read_text())['policies']['8']
            self.assertEqual(result['seed_frames'],[0]);self.assertEqual(result['prediction_frames'],2)
            self.assertEqual(result['references'],4);self.assertEqual(result['predictions'],2)
            self.assertEqual(result['matched_iou85'],2)
            self.assertEqual(result['per_label']['13']['predictions'],0)

    def test_batch_dryrun_has_no_client_calls_or_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);s=scope(root)
            items=[{'item':{'instance_key':'p1','label':'pallet','seed_frame':0},'targets':[1,2],'run_id':'one'}]
            with patch.object(batch.propagation,'run') as run:
                result=batch.execute(s,items,root/'work/out')
            run.assert_not_called();self.assertEqual(result['remote_calls'],0)
            self.assertFalse((root/'work/out').exists())

    def test_invalid_later_entry_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);s=scope(root)
            item={'label':'pallet','seed_frame':0,'seed_mask':'mask.png','seed_review':'review.json','steps':2,'instance_key':'p1'}
            (root/'batch.json').write_text(json.dumps({'instances':[item,item]}))
            with patch.object(guard,'load_baseline',return_value={}),patch.object(batch.propagation,'preflight',return_value={'targets':[1,2],'run_id':'one'}),patch.object(batch.propagation,'run') as run:
                with self.assertRaisesRegex(guard.GuardError,'DUPLICATED'):batch.prepare(s,root/'baseline',root/'batch.json')
            run.assert_not_called()

    def test_error_stops_queue_without_retry_or_secret_echo(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);s=scope(root)
            item={'label':'pallet','seed_frame':0,'seed_mask':'a','seed_review':'b','steps':2,'instance_key':'p1'}
            prepared=[{'item':item,'targets':[1,2],'run_id':'one','baseline':{}},
                      {'item':dict(item,instance_key='p2'),'targets':[1,2],'run_id':'one','baseline':{}}]
            with patch.object(batch.propagation,'run',side_effect=RuntimeError('secret-do-not-echo')) as run:
                result=batch.execute(s,prepared,root/'work/out',run=True)
            self.assertEqual(run.call_count,1)
            self.assertEqual(result['status'],'STOPPED_ON_ERROR_NO_AUTOMATIC_RETRY')
            self.assertNotIn('secret-do-not-echo',(root/'work/out/batch-summary.json').read_text())


if __name__=='__main__':unittest.main()

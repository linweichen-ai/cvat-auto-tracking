"""Non-secret scope configuration. Authentication is environment-only."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class Scope:
    base_url: str
    task_id: int
    job_id: int
    labels: dict
    frame_count: int
    width: int
    height: int
    function_id: str
    protected_frames: frozenset
    work_dir: Path
    images_dir: Path
    frame_pattern: str
    sequence: dict

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        return cls.from_dict(json.loads(path.read_text(encoding='utf-8-sig')), path.parent)

    @classmethod
    def from_dict(cls, value, root=Path('.')):
        required = {'base_url','task_id','job_id','labels','frame_count','width','height',
                    'function_id','protected_frames','work_dir','images_dir','frame_pattern','sequence'}
        if not isinstance(value, dict) or set(value) != required:
            raise ScopeError('CONFIG_FIELDS_INVALID_OR_MISSING')
        v = dict(value)
        parsed = urlsplit(v['base_url'])
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ScopeError('BASE_URL_MUST_BE_ORIGIN_WITHOUT_CREDENTIALS')
        v['base_url'] = v['base_url'].rstrip('/')
        for key in ('task_id','job_id','frame_count','width','height'):
            if type(v[key]) is not int or v[key] <= 0:
                raise ScopeError('POSITIVE_INTEGER_SCOPE_REQUIRED:' + key)
        if not isinstance(v['labels'],dict) or not v['labels'] or any(not isinstance(k,str) or not k or type(x) is not int or x <= 0 for k,x in v['labels'].items()):
            raise ScopeError('LABEL_NAME_TO_ID_MAP_REQUIRED')
        if len(set(v['labels'].values())) != len(v['labels']):
            raise ScopeError('DUPLICATE_LABEL_ID')
        if not isinstance(v['function_id'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',v['function_id']):
            raise ScopeError('INVALID_FUNCTION_ID')
        protected = v['protected_frames']
        if not isinstance(protected,list) or any(type(f) is not int or not 0 <= f < v['frame_count'] for f in protected) or len(set(protected)) != len(protected):
            raise ScopeError('INVALID_PROTECTED_FRAMES')
        v['protected_frames'] = frozenset(protected)
        root = Path(root).resolve()
        for key in ('work_dir','images_dir'):
            if not isinstance(v[key],str) or not v[key]:
                raise ScopeError('DIRECTORY_REQUIRED:' + key)
            v[key] = (root / v[key]).resolve()
        pattern = v['frame_pattern']
        if not isinstance(pattern,str) or not re.fullmatch(r'[^/\\{}]*\{frame(?::0?[1-9]\d*d)?\}[^/\\{}]*',pattern):
            raise ScopeError('FRAME_PATTERN_MUST_BE_LOCAL_FILENAME_WITH_FRAME_FIELD')
        if not isinstance(v['sequence'],dict) or v['sequence'].get('mode') not in ('timestamps','explicit'):
            raise ScopeError('SEQUENCE_MODE_REQUIRED')
        sequence = v['sequence']
        if sequence['mode'] == 'timestamps':
            if set(sequence) != {'mode','filename_regex','max_gap_seconds','source_index_step'}:
                raise ScopeError('TIMESTAMP_SEQUENCE_FIELDS_INVALID')
            regex = re.compile(sequence['filename_regex'])
            if not {'index','timestamp_ns'}.issubset(regex.groupindex):
                raise ScopeError('SEQUENCE_REGEX_NEEDS_NAMED_INDEX_AND_TIMESTAMP_NS')
            if type(sequence['source_index_step']) is not int or sequence['source_index_step'] <= 0 or type(sequence['max_gap_seconds']) not in (int,float) or not 0 < sequence['max_gap_seconds'] < float('inf'):
                raise ScopeError('INVALID_SEQUENCE_GAP_OR_STEP')
        else:
            if set(sequence) != {'mode','runs'} or not isinstance(sequence['runs'],list) or not sequence['runs']:
                raise ScopeError('EXPLICIT_SEQUENCE_RUNS_REQUIRED')
            seen = []
            for run in sequence['runs']:
                if not isinstance(run,list) or not run or any(type(f) is not int or not 0 <= f < v['frame_count'] for f in run):
                    raise ScopeError('EXPLICIT_SEQUENCE_FRAME_INVALID')
                seen.extend(run)
            if sorted(seen) != list(range(v['frame_count'])):
                raise ScopeError('EXPLICIT_RUNS_MUST_PARTITION_ALL_FRAMES_EXACTLY_ONCE')
        return cls(**v)

    def frame(self, frame, *, writable=False):
        if type(frame) is not int or not 0 <= frame < self.frame_count:
            raise ScopeError('FRAME_OUTSIDE_CONFIGURED_JOB')
        if writable and frame in self.protected_frames:
            raise ScopeError('HUMAN_PROTECTED_FRAME')
        return frame

    def image_path(self, frame):
        self.frame(frame)
        path = (self.images_dir / self.frame_pattern.format(frame=frame)).resolve()
        if path.parent != self.images_dir:
            raise ScopeError('SOURCE_IMAGE_ESCAPES_DIRECTORY')
        return path

    def output(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.work_dir):
            raise ScopeError('OUTPUT_OUTSIDE_CONFIGURED_WORK_DIR')
        return path

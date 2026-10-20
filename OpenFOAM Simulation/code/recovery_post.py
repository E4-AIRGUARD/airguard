"""Publish original postprocess.py CSV under clear filenames; preserve all values."""
import hashlib
import shutil
from pathlib import Path
from common import load,dump,require
from recovery_library import paths,source_info,MODEL

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def copy_verified(source,target):
    require(source.exists(),'Missing export source: '+str(source))
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        require(sha256(source)==sha256(target),'Existing output differs; inspect before replacing '+str(target))
        return
    temporary=target.with_suffix(target.suffix+'.partial')
    shutil.copy2(source,temporary)
    require(sha256(source)==sha256(temporary),'Export checksum mismatch')
    temporary.replace(target)

def publish_csv(pair,physics,root):
    info=source_info(pair,physics,root);p=paths(root);summary=load(info['summary_path'])
    require(summary.get('solver_complete'),'No completed CFD summary')
    target=p['csv']/f'{pair}_{physics}.csv';copy_verified(info['csv_path'],target)
    integration=Path(root)/'csv'/f'{info["case_directory"].name}_integration.json'
    if integration.exists():copy_verified(integration,p['meta']/f'{pair}_{physics}_integration.json')
    # Detailed numerical screening stays in the local postprocessing summary.
    # The shared sidecar records provenance without adding that discussion to the video.
    dump(p['meta']/f'{pair}_{physics}_source.json',dict(condition_id=pair,physics=physics,
         model=MODEL,source_case_id=info['case_directory'].name,
         source_csv=str(info['csv_path']),csv_sha256=sha256(target),
         numerical_screen=summary.get('numerical_screen'),physical_validation='not_performed',
         filename_note='Short filename; original CFD schema and source case ID are retained.'))
    print('Shared CSV:',target)

def publish_base_video(pair,root):
    info=source_info(pair,'BASE',root);p=paths(root);case=info['case_directory']
    source=Path(root)/'library'/case.name/f'{case.name}.mp4'
    copy_verified(source,p['video']/f'{pair}_BASE.mp4')
    copy_verified(source.parent/'video.json',p['meta']/f'{pair}_BASE_video.json')

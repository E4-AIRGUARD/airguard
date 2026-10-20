"""Three new conditions; original coupled CFD, fixed streamlines ONLY in video."""
import argparse
from contextlib import contextmanager
import os
import json
from pathlib import Path
import subprocess
import sys
from common import PROJECT, load, dump, csv_write, require, saved_times, verify

HERE=Path(__file__).resolve().parent
ROOT=PROJECT/'results_v04'
MIXED=('I100E60','I100E35','I60E35')
ARCHIVED=('I100E100','I60E60','I35E35')
ALIASES={'I60E100':'I100E60','I35E100':'I100E35','I35E60':'I60E35'}
MODEL='coupled_fluid_SST_legacy_V03_equations'
SETTINGS=dict(fps=20,video_s=60,speed_max_mps=3.3)

## External names are I100E60; old calculation IDs remain restart-compatible

def old_id(pair):return pair.replace('E','_E')

def paths(root):
    root=Path(root);share=root/'E4_AIRGUARD_배포'
    return dict(cases=root/'cases',share=share,csv=share/'01_CSV',
                video=share/'02_애니메이션',meta=share/'03_조건과품질',work=root/'render_work')

def source_info(pair,physics,root=ROOT):
    case=Path(root)/'cases'/f'{physics}_{old_id(pair)}_V04'
    return dict(case_directory=case,summary_path=Path(root)/'csv'/f'{case.name}_summary.json',
                csv_path=Path(root)/'csv'/f'{case.name}_timeseries.csv')

def pipeline(action,pair,physics,root,processes=None):
    cmd=[sys.executable,str(HERE/'e4_airguard_pipeline.py'),action,
         '--root',str(root),'--pair',old_id(pair),'--physics',physics]
    if action=='run':cmd+=['--skip-pilot']
    if action=='run' and processes is not None:cmd+=['--processes',str(processes)]
    subprocess.run(cmd,check=True)


def queue_paused(root,case):
    """A successful writeNow exit is a pause, not permission to post/run next."""
    return (Path(root)/'STOP_REQUESTED').exists() or load(case/'status.json')['status'] in ('PAUSED','INTERRUPTED')

def update_index(root):
    p=paths(root);rows=[]
    for pair in MIXED:
        for physics in ('BASE','SMOKE','HEAT'):
            info=source_info(pair,physics,root)
            summary=load(info['summary_path']) if info['summary_path'].exists() else {}
            video=p['video']/f'{pair}_{physics}.mp4'
            for condition in (pair,*[k for k,v in ALIASES.items() if v==pair]):
                rows.append(dict(condition_id=condition,representative_id=pair,is_alias=condition!=pair,
                    physics=physics,model=MODEL,flow_basis='assumed_equivalent_flow',
                    csv=f'01_CSV/{pair}_{physics}.csv',csv_ready=(p['csv']/f'{pair}_{physics}.csv').exists(),
                    video=f'02_애니메이션/{pair}_{physics}.mp4',video_ready=video.exists(),
                    numerical_screen=summary.get('numerical_screen','NOT_POSTPROCESSED'),
                    physical_validation='not_performed'))
    csv_write(p['meta']/'condition_index.csv',rows);dump(p['meta']/'condition_index.json',rows)
    dump(p['meta']/'existing_diagonal_references.json',load(PROJECT/'existing_BASE_on_D.json'))
    guide=PROJECT/'설명용_라이브러리_실행안내.md'
    if guide.exists():
        import shutil
        shutil.copy2(guide,p['share']/'README.md')


def compare(root,physics):
    from recovery_post import sha256
    p=paths(root);inputs=[p['video']/f'{pair}_{physics}.mp4' for pair in MIXED]
    require(all(x.exists() for x in inputs),'Render all three conditions before comparison')
    hashes={x.name:sha256(x) for x in inputs}
    target=p['video']/f'COMPARE_{physics}_I100E60_I100E35_I60E35.mp4'
    meta=p['meta']/f'COMPARE_{physics}.json'
    if target.exists() and meta.exists():
        previous=load(meta)
        require(previous['source_sha256']==hashes and previous['video_sha256']==sha256(target),'Comparison is stale')
        return
    require(not target.exists(),'Existing comparison has no completed metadata: '+str(target))
    p['work'].mkdir(parents=True,exist_ok=True);temporary=p['work']/f'COMPARE_{physics}.partial.mp4'
    cmd=['ffmpeg','-y','-loglevel','warning']
    for video in inputs:cmd+=['-i',str(video)]
    filters=';'.join(f'[{i}:v]scale=640:360,setsar=1[v{i}]' for i in range(3))
    filters+=';[v0][v1][v2]hstack=inputs=3[out]'
    cmd+=['-filter_complex',filters,'-map','[out]','-an','-c:v','libx264','-crf','23',
          '-pix_fmt','yuv420p','-movflags','+faststart',str(temporary)]
    subprocess.run(cmd,check=True)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                    '-show_entries','stream=nb_read_frames,duration','-of','json',str(temporary)]))['streams'][0]
    require(int(info['nb_read_frames'])==1200 and abs(float(info['duration'])-60)<.1,'Incomplete comparison video')
    temporary.replace(target);dump(meta,dict(source_sha256=hashes,video_sha256=sha256(target),order=list(MIXED)))
    print('Comparison:',target)

@contextmanager
def exclusive(root):
    import fcntl
    with (Path(root)/'library_queue.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another three-condition queue is running')
        yield

## All BASE -> all SMOKE -> all HEAT; strongest equivalent flow first

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=['plan','generate','status','produce','post','render','compare','index'])
    ap.add_argument('--root',type=Path,default=ROOT)
    ap.add_argument('--pair',choices=MIXED)
    ap.add_argument('--physics',choices=['all','BASE','SMOKE','HEAT'],default='all')
    ap.add_argument('--until',choices=['BASE','SMOKE','HEAT'],default='HEAT')
    ap.add_argument('--no-render',action='store_true',help='Produce CFD and CSV first; run render separately later')
    ap.add_argument('--processes',type=int,choices=(1,2,4,8),help='Use MPI for each case; keep the same count when resuming')
    a=ap.parse_args();root=a.root.resolve();pairs=(a.pair,) if a.pair else MIXED
    require(not a.no_render or a.action=='produce','--no-render applies to produce only')
    physics=('BASE','SMOKE','HEAT') if a.physics=='all' else (a.physics,)
    physics=tuple(ph for ph in physics if ('BASE','SMOKE','HEAT').index(ph)<=('BASE','SMOKE','HEAT').index(a.until))
    if a.action=='plan':
        print('Archived equal-stage conditions (not included): I100E100, I60E60, I35E35 -- NOT recalculated')
        print('Processes per case:',a.processes if a.processes is not None else 'preserve existing; new cases serial')
        for ph in physics:
            for pair in pairs:print(f'{pair}_{ph}: {source_info(pair,ph,root)["case_directory"]}')
        print('CFD: original fluid/SST/energy/scalar equations. Only video streamlines are fixed.');return
    if a.action=='status':
        for ph in physics:
            for pair in pairs:
                case=source_info(pair,ph,root)['case_directory']
                if not (case/'case.json').exists():print(pair,ph,'NOT_GENERATED');continue
                ts=saved_times(case,load(case/'case.json'))
                from parallel_support import processor_dirs,processor_times
                pt=processor_times(case,load(case/'case.json')) if processor_dirs(case) else []
                print(pair,ph,load(case/'status.json'), 'saved_s=',ts[-1][0] if ts else None,
                      'parallel_saved_s=',pt[-1][0] if pt else None)
        return
    if a.action=='index':update_index(root);return
    if a.action=='generate':
        for ph in physics:
            for pair in pairs:pipeline('generate',pair,ph,root)
        update_index(root);return
    require(os.name=='posix','Run in WSL after source /opt/openfoam14/etc/bashrc')
    with exclusive(root):
        for ph in physics:
            if a.action=='compare':compare(root,ph);continue
            for pair in pairs:
                if a.action=='produce':
                    pipeline('generate',pair,ph,root)
                    pipeline('run',pair,ph,root,a.processes)
                    case=source_info(pair,ph,root)['case_directory']
                    if queue_paused(root,case):
                        print('Paused. Saved fields preserved; rerun the same command to resume.');return
                if a.action in ('produce','post'):
                    pipeline('post',pair,ph,root)
                    from recovery_post import publish_csv
                    publish_csv(pair,ph,root)
                if a.action in ('produce','render') and not a.no_render:
                    case=source_info(pair,ph,root)['case_directory'];verify(case)
                    subprocess.run([sys.executable,str(HERE/'recovery_render.py'),str(case),
                                    '--root',str(root)],check=True)
                update_index(root)
            if not a.pair and a.action in ('produce','render') and not a.no_render:compare(root,ph)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:
        print('Stopped. Rerun the same command to resume saved fields.',file=sys.stderr);sys.exit(130)
    except Exception as e:print('ERROR:',e,file=sys.stderr);sys.exit(1)

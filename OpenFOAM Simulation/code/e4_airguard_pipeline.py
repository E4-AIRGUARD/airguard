"""E4-AIRGUARD lifecycle: generate -> run -> post -> render -> index. Python standard library."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from common import PROJECT, MODES, LEVELS, PHYSICS, load, dump, require, validate, digest, saved_times, verify, csv_write, field_text
from case_builder import generate
from common import require_screen,parse_log_time,active_pairs,restart_time_name
from parallel_support import process_count,prepare_parallel,reconstruct_saved,solver_command,processor_times,processor_dirs

HERE=Path(__file__).resolve().parent
RUN_LEVELS = LEVELS

def run_order(cases):
    # Preserve BASE dependencies, then run faster fan levels first per physics.
    def key(case):
        meta=load(case/'case.json')
        return MODES.index(meta.get('mode','BOTH')),PHYSICS.index(meta['physics']),RUN_LEVELS.index(meta['condition_id'])
    return sorted(cases,key=key)
def stamp(): return datetime.now().strftime('%Y%m%d_%H%M%S_%f')

def doctor():
    result={'platform':sys.platform,'OpenFOAM_version':os.environ.get('WM_PROJECT_VERSION'),'tools':{k:shutil.which(k) for k in ('blockMesh','checkMesh','foamRun','foamDictionary','pvpython','ffmpeg','ffprobe','xvfb-run','xauth','mpirun','decomposePar','reconstructPar')}}
    result['ready_solver']=result['OpenFOAM_version']=='14' and all(result['tools'][k] for k in ('blockMesh','checkMesh','foamRun','foamDictionary'))
    result['ready_parallel']=result['ready_solver'] and all(result['tools'][k] for k in ('mpirun','decomposePar','reconstructPar'))
    print(json.dumps(result,indent=2));return result

def foam_ready():
    require(os.name=='posix' and os.environ.get('WM_PROJECT_VERSION')=='14','Run in WSL after source /opt/openfoam14/etc/bashrc')
    for cmd in ('blockMesh','checkMesh','foamRun','foamDictionary'):require(shutil.which(cmd), 'Missing '+cmd)

def set_entry(case,key,value):
    subprocess.run(['foamDictionary',str(case/'system/controlDict'),'-entry',key,'-set',str(value)],check=True,stdout=subprocess.DEVNULL)

def expose_full_time_window(case):
    """Let ParaView see 0..end after a solver restart changed startTime."""
    # A resumed solver leaves startTime at its checkpoint (for example 192 s).
    # The OpenFOAM reader honours that value and otherwise hides earlier, valid
    # saved fields.  Completed cases are verified before this helper is called,
    # so resetting the reader window does not alter any calculated field.
    set_entry(case,'startFrom','startTime')
    set_entry(case,'startTime','0')
    set_entry(case,'stopAt','endTime')

def execute(cmd,case,root):
    # Serial and MPI solver logs use one namespace for completion validation.
    label='foamRun' if 'foamRun' in cmd else Path(cmd[0]).name
    log=case/f'log.{label}.{stamp()}'
    print(f'{case.name}: {" ".join(cmd)} | {log.name}',flush=True)
    last_report=-999;clock=time.monotonic()
    with log.open('w',encoding='utf-8') as f:
        env=os.environ.copy()
        if cmd[0]=='mpirun':env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        proc=subprocess.Popen(cmd,cwd=case,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',start_new_session=True)
        dump(root/'active.json',{'case':str(case),'pid':proc.pid,'command':cmd,'log':str(log)})
        try:
            for line in proc.stdout:
                f.write(line)
                t=parse_log_time(line)
                if t is not None:
                    f.flush()
                    if t-last_report>=5 or time.monotonic()-clock>60:
                        print(f'  CFD time {t:g} s',flush=True);last_report=t;clock=time.monotonic()
            code=proc.wait()
        except KeyboardInterrupt:
            os.killpg(proc.pid,signal.SIGINT)
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGTERM);proc.wait()
            raise
        finally:
            # Keep coordinator errors from leaving a detached solver running.
            if proc.poll() is None:
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    proc.wait()
            (root/'active.json').unlink(missing_ok=True)
    require(code==0, f'Command failed ({code}): {log}')
    text=log.read_text(errors='replace');require('FOAM FATAL' not in text, 'FOAM FATAL: '+str(log))
    if cmd[0]=='checkMesh':require('Mesh OK.' in text,'Mesh check failed: '+str(log))
    return log

def inherit(case,root,m):
    if m['physics']=='BASE' or m['config']['initial_flow']=='rest':return
    flag=case/'initialization.json'
    if flag.exists():return
    cid='BASE_'+m['condition_id']+'_'+m['config']['revision']+('_PILOT' if m['pilot'] else '')
    source=root/'cases'/cid
    verify(source)
    sm=load(source/'case.json')
    source_config=dict(sm['config']);target_config=dict(m['config'])
    # BASE is isothermal; a HEAT-only initial temperature change does not alter its flow.
    if m['physics']=='HEAT':
        source_config.pop('heat_initial_c',None);target_config.pop('heat_initial_c',None)
    require(source_config==target_config,'Baseline config mismatch')
    require(sm.get('mode','BOTH')==m.get('mode','BOTH'),'Baseline fan mode mismatch')
    if not m['pilot']:
        summary=root/'csv'/f'{cid}_summary.json'
        require(summary.exists(),f'Run post --mode {m.get("mode","BOTH")} --physics BASE before {m["physics"]}')
        require_screen(load(summary),sm,baseline=True)
    # Universal-newline decoding permits Windows CRLF and Linux LF copies.
    # All other dictionary content must still match exactly.
    require((source/'system/blockMeshDict').read_text(encoding='utf-8')==(case/'system/blockMeshDict').read_text(encoding='utf-8'),'Mesh mismatch')
    t,folder=saved_times(source,sm)[-1]
    # Keep new p/p_rgh and T: heat density/hydrostatics differ. Pressure re-equilibrates.
    for name in ['U']+(['k','omega','nut','alphat'] if m['config']['flow_model']=='sst' else []):
        (case/'0'/name).write_text(field_text(folder,name),encoding='utf-8')
    dump(flag,{'baseline_case':str(source),'source_time_s':t,'copied_fields':['U','k','omega','nut','alphat'] if m['config']['flow_model']=='sst' else ['U'],'pressure_reinitialized':True,'thermal_and_scalar_reset':True,'pilot_initialization_not_steady':m['pilot']})

def run_case(case,root,processes=None):
    import fcntl
    with (case/'run.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another solver owns '+str(case))
        m=load(case/'case.json')
        if load(case/'status.json')['status']=='SOLVER_COMPLETE':verify(case);print('Already complete:',case.name);return
        try:
            n=process_count(case,processes)
            if n>1:reconstruct_saved(case,root,m,execute)
            # Check and copy the matching BASE state before spending time meshing.
            pre_times=saved_times(case,m);pre_latest=pre_times[-1][0] if pre_times else 0
            if pre_latest==0:inherit(case,root,m)
            if not (case/'constant/polyMesh/boundary').exists():execute(['blockMesh'],case,root)
            execute(['checkMesh'],case,root)
            times=saved_times(case,m);latest=times[-1][0] if times else 0
            if abs(latest-m['end'])<1e-6:
                # Recover bookkeeping if the coordinator stopped after solver End.
                dump(case/'status.json',verify(case));return
            require(latest<m['end']-1e-6, 'Final fields exist without validated completion. Inspect logs; do not overwrite automatically.')
            # Do not round an off-grid pause checkpoint to six significant digits.
            require(times,'No complete saved fields; inspect the case before restarting')
            set_entry(case,'startFrom','startTime');set_entry(case,'startTime',restart_time_name(times));set_entry(case,'stopAt','endTime')
            if n>1:prepare_parallel(case,root,m,n,restart_time_name(times),execute)
            dump(case/'status.json',{'status':'RUNNING','resume_time_s':latest,'processes':n})
            execute(solver_command(n),case,root)
            if n>1:reconstruct_saved(case,root,m,execute)
            if (root/'STOP_REQUESTED').exists():
                try:status=verify(case)
                except ValueError:status={'status':'PAUSED'}
                dump(case/'status.json',status);return
            dump(case/'status.json',verify(case))
        except KeyboardInterrupt:
            dump(case/'status.json',{'status':'INTERRUPTED','message':'Resume from last complete saved time'});raise
        except Exception as e:
            dump(case/'status.json',{'status':'FAILED','error':str(e)});raise

def paraview_env():
    env=os.environ.copy();env.pop('PYTHONHOME',None)
    require(shutil.which('pvpython'),'pvpython not found')
    cmd=['pvpython','-c','from paraview.simple import OpenFOAMReader; import vtk, numpy; print("PASS")']
    # Test clean environment first; distro fallback only if needed.
    env.pop('PYTHONPATH',None)
    a=subprocess.run(cmd,env=env,capture_output=True,text=True)
    if a.returncode:
        env['PYTHONPATH']='/usr/lib/python3/dist-packages'
        a=subprocess.run(cmd,env=env,capture_output=True,text=True)
    require(a.returncode==0,'ParaView import failed: '+a.stderr[-2000:])
    return env

def selected(root,mode,physics,level,pilot):
    batch=load(root/'batch.json');result=[]
    pairs=active_pairs(batch['config'])
    require(level=='all' or level in pairs,'Pair excluded from new calculation; see README.md: archived equal-stage conditions')
    for md in MODES:
        for p in PHYSICS:
            for l in pairs:
                if mode not in ('all',md) or physics not in ('all',p) or level not in ('all',l):continue
                case=root/'cases'/f'{p}_{l}_{batch["config"]["revision"]}{"_PILOT" if pilot else ""}'
                if case.exists():result.append(case)
    require(result,'No matching cases; generate first');return result

def index(root):
    rows=[]
    pairs=active_pairs(load(root/'batch.json')['config'])
    for cp in sorted((root/'cases').glob('*/case.json')):
        m=load(cp);cid=m['case_id'];case=cp.parent
        if m['condition_id'] not in pairs:continue
        row={'case_id':cid,'mode':m.get('mode','BOTH'),'physics':m['physics'],'condition_id':m['condition_id'],'pilot':m['pilot'],'status':load(case/'status.json')['status'],'config_hash':m['config_hash'],'case_setup_hash':m.get('case_setup_hash'),'flow_basis':m['flow_basis'],'velocity_mps':m['velocity'],'equivalent_flow_group':m.get('equivalent_flow_group'),'fans_individually_modeled':False,'intake_pwm_percent':m.get('intake_pwm_percent'),'exhaust_pwm_percent':m.get('exhaust_pwm_percent'),'boundary_model':m.get('boundary_model'),'case_path':str(case.relative_to(root)),'csv_path':'','video_path':'','preview_path':'','quality':'not_reviewed'}
        summary=root/'csv'/f'{cid}_summary.json'
        if summary.exists():row.update(load(summary));row['csv_path']=f'csv/{cid}_timeseries.csv'
        vm=root/'library'/cid/'video.json'
        if vm.exists():
            v=load(vm);row['video_path']=v['video_path'];row['preview_path']=v['preview_path']
        rows.append(row)
    if rows:csv_write(root/'metadata/library_index.csv',rows)
    dump(root/'metadata/library_index.json',rows)
    print('Index:',root/'metadata/library_index.json')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','doctor','generate','run','stop','status','post','render','index','compare'])
    p.add_argument('--config',type=Path,default=PROJECT/'PIPELINE_CONFIG.json')
    p.add_argument('--root',type=Path,default=PROJECT/'results_v04')
    p.add_argument('--mode',choices=['all',*MODES],default='BOTH')
    p.add_argument('--physics',choices=['all',*PHYSICS],default='BASE')
    p.add_argument('--level','--pair',dest='level',choices=['all',*LEVELS],default='all')
    p.add_argument('--pilot',action='store_true',help='Separate 2s cases, not production results')
    p.add_argument('--skip-pilot',action='store_true',help='Explicitly skip the pilot prerequisite for a production run; retain mesh and CSV checks')
    p.add_argument('--snapshot',action='store_true')
    p.add_argument('--processes',type=int,choices=(1,2,4,8),help='MPI processes; default preserves an existing parallel case, otherwise serial')
    a=p.parse_args();root=a.root.resolve()
    require(not a.skip_pilot or (a.action=='run' and not a.pilot),'--skip-pilot requires production run (without --pilot)')
    if a.action=='doctor':doctor();return
    if a.action=='plan':
        c=load(a.config);validate(c,False)
        pairs=active_pairs(c)
        for l in LEVELS:print(l,'NEW' if l in pairs else 'ARCHIVED: excluded',c['levels'][l])
        unset=any(c['levels'][l]['velocity_mps'] is None for l in pairs)
        print(('Velocity unset => generate/run blocked.' if unset else 'Nominal velocities configured; installed flow unvalidated.')+f' {len(pairs)} active cases. Intake-major HIGH/MID/LOW order. No CFD started.');return
    if a.action=='generate':
        c=load(a.config);validate(c)
        pairs=active_pairs(c)
        require(a.level=='all' or a.level in pairs,'Pair excluded from new calculation; see README.md: archived equal-stage conditions')
        require(not a.pilot or c['field_interval_s']<=2 and abs(2/c['field_interval_s']-round(2/c['field_interval_s']))<1e-8,'Pilot requires interval dividing 2s')
        root.mkdir(parents=True,exist_ok=True)
        if (root/'batch.json').exists():require(load(root/'batch.json')['config_hash']==digest(c),'Config changed; choose new --root and revision')
        else:dump(root/'batch.json',{'config':c,'config_hash':digest(c),'created_at':stamp()})
        modes=MODES if a.mode=='all' else (a.mode,)
        for md in modes:
            for ph in PHYSICS:
                for lv in pairs:
                    if a.physics not in ('all',ph) or a.level not in ('all',lv):continue
                    cid=f'{ph}_{lv}_{c["revision"]}'+('_PILOT' if a.pilot else '')
                    case=root/'cases'/cid
                    if case.exists():
                        require(load(case/'case.json')['config_hash']==digest(c),'Existing case config mismatch: '+cid)
                        print('Preserved existing:',cid);continue
                    generate(case,c,ph,lv,2 if a.pilot else None,a.pilot,md);print('Generated:',cid)
        index(root);return
    require((root/'batch.json').exists(),'No batch.json under --root')
    if a.action=='index':index(root);return
    if a.action=='stop':
        foam_ready();active=root/'active.json';require(active.exists(),'No active command recorded')
        d=load(active);require('foamRun' in d['command'],'Currently preparing/reconstructing; retry stop once solver starts')
        case=Path(d['case']).resolve();require(case.is_relative_to(root/'cases'),'Active case outside root')
        (root/'STOP_REQUESTED').touch();set_entry(case,'stopAt','writeNow')
        print('Graceful stop requested; wait for PAUSED. Remaining queue will not start.');return
    cases=selected(root,a.mode,a.physics,a.level,a.pilot)
    if a.action=='status':
        for case in cases:
            m=load(case/'case.json');t=saved_times(case,m)
            pt=processor_times(case,m) if processor_dirs(case) else []
            print(case.name,load(case/'status.json'),'saved_s=',t[-1][0] if t else None,
                  'parallel_saved_s=',pt[-1][0] if pt else None)
        return
    if a.action=='run':
        foam_ready()
        import fcntl
        with (root/'pipeline.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('Another run queue owns this root')
            (root/'STOP_REQUESTED').unlink(missing_ok=True)
            for case in run_order(cases):
                m=load(case/'case.json')
                if not a.pilot and not a.skip_pilot:
                    pilot_case=root/'cases'/(m['case_id']+'_PILOT')
                    require((pilot_case/'case.json').exists(),'Generate/run the matching --pilot case first: '+pilot_case.name)
                    verify(pilot_case)
                    ps=root/'csv'/f'{pilot_case.name}_summary.json'
                    require(ps.exists(),'Run post --pilot before production')
                    require_screen(load(ps),load(pilot_case/'case.json'))
                if a.skip_pilot:
                    dump(case/f'pilot_waiver.{stamp()}.json',{'case_id':m['case_id'],'config_hash':m['config_hash'],'reason':'Explicit --skip-pilot requested; mesh and postprocessing checks remain enabled'})
                run_case(case,root,a.processes);index(root)
                if (root/'STOP_REQUESTED').exists():break
        return
    if a.action=='compare':
        require(a.physics!='all','Choose --physics BASE/HEAT/SMOKE')
        require(a.mode!='all','Choose --mode BOTH/INTAKE/EXHAUST')
        require(not a.pilot and not a.snapshot,'Compare production MP4 only')
        require(shutil.which('ffmpeg'),'ffmpeg missing')
        expected=active_pairs(load(root/'batch.json')['config'])
        require(len(cases)==len(expected) and len(cases)>=2,'All active combinations required for comparison')
        cases=run_order(cases)
        videos=[];manifests=[]
        for case in cases:
            cid=load(case/'case.json')['case_id'];out=root/'library'/cid
            manifests.append(load(out/'frames.json'));videos.append(out/f'{cid}.mp4')
        require(all(v.exists() for v in videos),'Missing video')
        require(all([f['CFD_time_s'] for f in x]==[f['CFD_time_s'] for f in manifests[0]] for x in manifests),'Time axes differ')
        columns=2 if len(cases)==6 else 3
        rows=(len(cases)+columns-1)//columns
        output=root/'library'/f'{a.physics}_ACTIVE_{rows}rows_{columns}cols.mp4'
        cmd=['ffmpeg','-n']
        for v in videos:cmd+=['-i',str(v)]
        layout='|'.join(f'{i%columns*640}_{i//columns*400}' for i in range(len(cases)))
        filters=';'.join(f'[{i}:v]scale=640:400,setsar=1[v{i}]' for i in range(len(cases)))
        filters+=';'+''.join(f'[v{i}]' for i in range(len(cases)))+f'xstack=inputs={len(cases)}:layout={layout}:fill=white[v]'
        cmd+=['-filter_complex',filters,'-map','[v]','-c:v','libx264','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(output)]
        subprocess.run(cmd,check=True);print(output);return
    env=paraview_env()
    for case in run_order(cases):
        verify(case)
        expose_full_time_window(case)
        if a.action=='post':cmd=['pvpython',str(HERE/'postprocess.py'),str(case),'--root',str(root)]
        else:
            require((root/'csv'/f'{case.name}_summary.json').exists(),'Postprocess before rendering')
            require(not a.snapshot,'Snapshot is not included; use recovery_library.py render')
            cmd=[sys.executable,str(HERE/'recovery_render.py'),str(case),'--root',str(root)]
        subprocess.run(cmd,env=env,check=True)
        index(root)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('Stopped. Saved fields preserved.',file=sys.stderr);sys.exit(130)
    except Exception as e:print('ERROR:',e,file=sys.stderr);sys.exit(1)

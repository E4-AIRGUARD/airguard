"""Serial-to-MPI restart support for Foundation 14, static uncollated cases.

Never delete processor folders or use decomposePar -force / reconstructPar -rm.
Complete root fields remain the source used by CSV and ParaView.
"""
import os
from pathlib import Path
import re
import shutil
from common import dump, load, require, saved_times


def processor_dirs(case):
    case=Path(case)
    require(not any(p.is_dir() for p in case.glob('processors*')),
            'Collated processor data is not supported by this resume tool')
    directories=sorted((p for p in case.glob('processor*')
                        if p.is_dir() and re.fullmatch(r'processor\d+',p.name)),
                       key=lambda p:int(p.name[9:]))
    require([p.name for p in directories]==[f'processor{i}' for i in range(len(directories))],
            'Incomplete processor directory set; preserve data and inspect decomposition')
    return directories


def process_count(case, requested=None):
    state=Path(case)/'parallel_state.json'
    previous=load(state)['processes'] if state.exists() else None
    n=requested if requested is not None else previous or 1
    require(n in (1,2,4,8),'Processes must be 1, 2, 4 or 8')
    require(previous is None or previous==n,
            f'This case uses {previous} processes; resume with the same count to preserve its decomposition')
    if n>1:
        available=len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else os.cpu_count() or 1
        require(n<=available,f'{n} MPI processes requested, but only {available} CPUs are available')
        for tool in ('mpirun','decomposePar','reconstructPar'):
            require(shutil.which(tool),'Missing parallel tool: '+tool)
    if processor_dirs(case):require(previous is not None,'Unmanaged processor folders; inspect before resuming')
    return n


def processor_times(case,meta):
    """Only times complete on every processor can be resumed/reconstructed."""
    dirs=processor_dirs(case)
    if not dirs:return []
    state=load(Path(case)/'parallel_state.json')
    require(len(dirs)==state['processes'],'Processor count does not match parallel_state.json')
    times=[{folder.name:(t,folder) for t,folder in saved_times(d,meta)} for d in dirs]
    shared=set.intersection(*(set(t) for t in times))
    return sorted((times[0][name] for name in shared),key=lambda item:item[0])


def reconstruction_times(case,meta):
    available=processor_times(case,meta)
    complete={t for t,_ in saved_times(case,meta)}
    return [(t,folder) for t,folder in available if t not in complete]


def reconstruct_saved(case,root,meta,execute):
    """Recover missing/partially written root times, without overwriting good ones."""
    if not processor_dirs(case):return
    state=load(case/'parallel_state.json')
    require(state['config_hash']==meta['config_hash'],'Parallel config hash mismatch')
    pending=reconstruction_times(case,meta)
    if not pending:return
    # Explicit times also repair an interrupted reconstruction. -newTimes would
    # incorrectly skip an existing but incomplete root time directory.
    selection=','.join(folder.name for _,folder in pending)
    execute(['reconstructPar','-time',selection,'-fileHandler','uncollated'],case,root)
    complete={t for t,_ in saved_times(case,meta)}
    require(all(t in complete for t,_ in pending),'Reconstruction did not produce every requested complete time')
    dump(case/'reconstruction.json',dict(processes=state['processes'],
         reconstructed_times_s=[t for t,_ in pending],latest_root_time_s=max(complete),
         processor_data_preserved=True))


def prepare_parallel(case,root,meta,n,start_name,execute):
    state_path=case/'parallel_state.json'
    if state_path.exists():
        state=load(state_path)
        require(state['processes']==n and state['config_hash']==meta['config_hash'],
                'Parallel setup mismatch')
    else:
        require(not processor_dirs(case),'Refusing to overwrite unmanaged processor data')
        dump(state_path,dict(processes=n,config_hash=meta['config_hash'],
             method='scotch',file_handler='uncollated',first_decomposition_time=start_name))
    dictionary=('FoamFile\n{\n    version 2.0;\n    format ascii;\n'
                '    class dictionary;\n    object decomposeParDict;\n}\n'
                f'numberOfSubdomains {n};\nmethod scotch;\n')
    target=case/'system/decomposeParDict'
    if target.exists():
        require(target.read_text(encoding='utf-8')==dictionary,'Existing decomposition dictionary differs')
    else:target.write_text(dictionary,encoding='utf-8',newline='\n')
    dirs=processor_dirs(case)
    if dirs:
        require(len(dirs)==n and all((d/'constant/polyMesh/boundary').exists() for d in dirs),
                'Decomposition was interrupted; processor data preserved, inspect before retrying')
        if start_name in {p.name for _,p in processor_times(case,meta)}:return
    cmd=['decomposePar','-time',start_name,'-fileHandler','uncollated']
    if dirs:cmd+=['-fields']
    execute(cmd,case,root)
    require(start_name in {p.name for _,p in processor_times(case,meta)},
            'Decomposition did not create complete restart fields on all processors')


def solver_command(n):
    if n==1:return ['foamRun','-solver','fluid']
    return ['mpirun','-np',str(n),'--bind-to','core',
            'foamRun','-solver','fluid','-parallel','-fileHandler','uncollated']

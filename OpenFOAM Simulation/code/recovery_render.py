"""Render original coupled CFD, fixed representative flow and changing saved T/C."""
import argparse
import json
from pathlib import Path
import secrets
import shutil
import subprocess
from common import load,dump,require,verify
from recovery_library import paths,SETTINGS,MODEL
from e4_airguard_pipeline import paraview_env
from recovery_post import sha256


def quality_label(summary,physics):
    require(summary.get('solver_complete'),'CFD incomplete')
    if summary.get('numerical_screen')=='PASS':return 'CFD reference | assumed equivalent flow'
    failures=set(summary.get('numerical_failures',[]))
    require(physics=='HEAT' and failures=={'temperature_outside_expected_range'},
            'Rendering blocked by numerical failures: '+str(sorted(failures)))
    return 'CFD reference | assumed equivalent flow'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('case',type=Path);ap.add_argument('--root',type=Path,required=True)
    a=ap.parse_args();p=paths(a.root);m=load(a.case/'case.json');physics=m['physics'];pair=m['condition_id'].replace('_','')
    verify(a.case)
    summary_path=a.root/'csv'/f'{m["case_id"]}_summary.json'
    q=load(summary_path);label=quality_label(q,physics)
    if physics=='BASE':require(q.get('baseline_ready'),'BASE tail not ready')
    for name in ('ffmpeg','ffprobe','xvfb-run','xauth'):require(shutil.which(name),'Missing '+name)
    target=p['video']/f'{pair}_{physics}.mp4';manifest=p['meta']/f'{pair}_{physics}_video.json'
    if target.exists() and manifest.exists():
        vm=load(manifest)
        require(sha256(target)==vm['video_sha256'] and sha256(summary_path)==vm['summary_sha256'], 'Stale or altered video')
        print('Verified video retained:',target);return
    require(not target.exists(),'Existing video has no completed manifest; inspect '+str(target))
    env=paraview_env();env['LIBGL_ALWAYS_SOFTWARE']='1'
    p['video'].mkdir(parents=True,exist_ok=True)
    work=p['work']/f'{pair}_{physics}';work.mkdir(parents=True,exist_ok=True)
    temporary=work/'encoding.partial.mp4'
    cmd=['pvpython',str(Path(__file__).with_name('recovery_scene.py')),str(a.case),
         '--csv',str(a.root/'csv'/f'{m["case_id"]}_timeseries.csv'),
         '--video',str(temporary),'--work',str(work),'--quality-label',label]
    for attempt in range(3):
        wrapped=['xvfb-run','-a','-n',str(100+secrets.randbelow(30000)),
                 '-e',str(work/f'xvfb_{attempt}.log'),'-s','-screen 0 1920x1080x24 -nolisten tcp',
                 '/usr/bin/python3',str(Path(__file__).with_name('x_display_preflight.py')),*cmd]
        result=subprocess.run(wrapped,env=env)
        if result.returncode==0:break
        if result.returncode!=75:raise subprocess.CalledProcessError(result.returncode,wrapped)
    else:raise RuntimeError('X display could not start; see '+str(work))
    result=subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
         '-show_entries','stream=nb_read_frames,width,height,duration','-of','json',str(temporary)],
         capture_output=True,text=True,check=True)
    video=json.loads(result.stdout)['streams'][0]
    require(int(video['nb_read_frames'])==SETTINGS['fps']*SETTINGS['video_s'],'Encoded frame count mismatch')
    require(abs(float(video['duration'])-SETTINGS['video_s'])<.1,'Encoded duration mismatch')
    require(int(video['width'])==1600 and int(video['height'])==900,'Encoded size mismatch')
    temporary.replace(target);frames=load(work/'frames.json')
    dump(manifest,dict(condition_id=pair,physics=physics,video=target.name,**video,
         CFD_start_s=frames[0]['CFD_time_s'],CFD_end_s=frames[-1]['CFD_time_s'],
         scalar_sampling='nearest_saved_time',streamlines='fixed_final_'+physics,
         markers='illustrative_direction_not_particle_speed',model=MODEL,
         numerical_screen=q['numerical_screen'],quality_label=label,physical_validation='not_performed',
         video_sha256=sha256(target),summary_sha256=sha256(summary_path)))
    print('Video:',target)

if __name__=='__main__':main()

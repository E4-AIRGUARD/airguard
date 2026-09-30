"""Run a 5-minute fan-off baseline and then nine 5-minute clean-air combinations."""
import json
import sys
from datetime import datetime
from pathlib import Path

import measure_clean_air_matrix as matrix
import measure_thermal_response as thermal


def main():
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
    root = matrix.DEFAULT_ROOT / ('session_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True, exist_ok=False)
    stop_file = root / 'STOP'
    state = {'started':datetime.now().isoformat(timespec='seconds'), 'status':'running',
             'stage':'phase0_baseline', 'baseline_complete':False, 'matrix_complete':False,
             'baseline_sec':300, 'seconds_per_combination':300, 'image_interval_sec':1,
             'stop_file':str(stop_file.resolve())}
    def save():
        (root / 'session.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    print(f'[session] ROOT {root}',flush=True)
    try:
        complete = thermal.main(['phase0_baseline','--duration-min','5','--intake','0','--exhaust','0',
                                 '--out-dir',str(root/'baseline'/'csv'),
                                 '--image-dir',str(root/'baseline'/'images'),
                                 '--stop-file',str(stop_file)])
        if not complete:
            state['status']='stopped'
            return
        state['baseline_complete']=True
        state['stage']='fan_matrix'
        save()
        matrix.main(['--duration-sec','300','--image-interval-sec','1',
                     '--out-root',str(root/'fan_matrix'),'--stop-file',str(stop_file)])
        batch = next((root/'fan_matrix').glob('*/batch.json'))
        batch_state = json.loads(batch.read_text(encoding='utf-8'))
        state['matrix_complete']=batch_state['status']=='complete'
        state['status']=batch_state['status']
        state['stage']='finished' if state['matrix_complete'] else 'fan_matrix'
    except BaseException as exc:
        state['status']='stopped' if isinstance(exc,KeyboardInterrupt) else 'failed'
        state['error']=str(exc)
        raise
    finally:
        state['ended']=datetime.now().isoformat(timespec='seconds')
        save()
        print(f"[session] {state['status']} -> {root}",flush=True)


if __name__=='__main__':
    main()

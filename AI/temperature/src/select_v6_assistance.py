"""Freeze assistance using paired validation replays only, before test execution."""
import argparse
import json
from pathlib import Path

import pandas as pd
import torch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--comparison',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    definitions=json.loads((args.comparison/'definitions.json').read_text())
    if definitions['settings']['split']!='validation':raise ValueError('Only validation may select assistance')
    rows=pd.read_csv(args.comparison/'summary.csv')
    chosen=rows.sort_values(['normal_fan_up_count','prewarning_success','median_lead_s','total_pwm_changes'],
                           ascending=[True,False,False,True]).iloc[0]
    artifact=torch.load(args.model,map_location='cpu',weights_only=False)
    artifact['recommended_raw_rise_assist']=bool(chosen['mode']=='on')
    artifact['assistance_selection']='Validation: minimize no-heat upward commands, maximize early events and median lead, then minimize switches.'
    args.output.mkdir(parents=True,exist_ok=False)
    torch.save(artifact,args.output/'lstm_temperature_trajectory_v6.pt')
    (args.output/'selection.json').write_text(json.dumps(dict(selected_mode=chosen['mode'],validation=rows.to_dict('records'),
        selection=artifact['assistance_selection'],test_used=False),indent=2),encoding='utf-8')
    print('Frozen assistance:',chosen['mode'],flush=True)


if __name__=='__main__':main()

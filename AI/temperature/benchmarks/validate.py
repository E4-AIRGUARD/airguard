"""Re-evaluate shipped models on the frozen test split without hardware access."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'training')]
import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import precision_recall_fscore_support
from project_paths import TRAINING_CSV, SPLIT_CSV, MODELS_DIR
from train_lstm_v6 import build_items, predict
from trajectory_lstm_v6 import TrajectoryLSTM


def metrics(actual, prediction):
    error=prediction-actual
    positive=actual>=26; alarm=prediction>=26
    p,r,f,_=precision_recall_fscore_support(positive,alarm,average='binary',zero_division=0)
    return dict(samples=len(actual),mae_c=float(np.abs(error).mean()),
        rmse_c=float(np.sqrt(np.mean(error**2))),precision=float(p),recall=float(r),f1=float(f),
        fp=int((~positive & alarm).sum()),fn=int((positive & ~alarm).sum()),
        tp=int((positive & alarm).sum()),tn=int((~positive & ~alarm).sum()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'results/validation')
    args=parser.parse_args(); torch.set_num_threads(1)
    artifact=torch.load(MODELS_DIR/'lstm_temperature_trajectory_v6.pt',map_location='cpu',weights_only=False)
    for path,key in ((TRAINING_CSV,'training_csv_sha256'),(SPLIT_CSV,'split_csv_sha256')):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=artifact[key]:
            raise ValueError(f'Dataset hash mismatch: {path}')
    data=pd.read_csv(TRAINING_CSV)
    data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda t:t.timestamp())
    manifest=pd.read_csv(SPLIT_CSV)
    names=set(manifest.loc[manifest.split=='test','run'])
    items=[i for i in build_items(data) if i['run'] in names]
    model=TrajectoryLSTM(artifact['feature_mean'],artifact['feature_std'])
    model.load_state_dict(artifact['state_dict']); model.eval()
    predictions,_,_=predict(model,items,np.array(artifact['feature_mean']),np.array(artifact['feature_std']))
    baseline=joblib.load(MODELS_DIR/'physics_mpc_model.pkl')
    for key in ('training_csv_sha256','split_csv_sha256'):
        if baseline[key]!=artifact[key]: raise ValueError('Baseline split differs from v6.')
    values=np.array([i['temp'] for i in items],dtype=float)
    rate=np.array([i['history'][-1,1] for i in items],dtype=float)
    intake=np.array([i['intake'] for i in items]); exhaust=np.array([i['exhaust'] for i in items])
    for _ in range(5):
        gap=np.maximum(values-baseline['ambient_temp_c'],0)
        rate=(baseline['intercept']+baseline['env_gain']*(baseline['ambient_temp_c']-values)
              +baseline['trend_gain']*np.clip(rate,-2,2)
              -(baseline['intake_fan_gain']*intake/100+baseline['exhaust_fan_gain']*exhaust/100)*gap)
        values+=rate
    actual=np.array([i['trajectory_target'][2] for i in items])
    report={'regression':metrics(actual,values),'lstm_v6':metrics(actual,predictions[:,2]),
            'limitation':'Repeated development test, not a new closed-loop trial or latency benchmark.'}
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'metrics.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()

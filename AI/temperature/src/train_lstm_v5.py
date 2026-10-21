"""Retrain v5 with validation-selected stationary-region loss, preserving run splits."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn

from improve_lstm_stability import examples, score
from multirate_temperature_lstm import MultiRateTemperatureLSTM
from project_paths import TRAINING_CSV, SPLIT_CSV, RESULTS_DIR, MODELS_DIR
from train_multirate_rise_lstm import arrays, predictions
from main_runner import LstmMpcController


def fit(train,valid,stationary_weight,epochs):
    torch.manual_seed(42)
    x,y=arrays(train);vx,vy=arrays(valid)
    mean=x.mean((0,1));std=np.maximum(x.std((0,1)),1e-3)
    stable=np.array([abs(i['target']-i['temp'])<=.05 and abs(i['history'][-1,5])<=.02 for i in train])
    vstable=np.array([abs(i['target']-i['temp'])<=.05 and abs(i['history'][-1,5])<=.02 for i in valid])
    counts=pd.Series([i['run'] for i in train]).value_counts()
    weight=np.array([1/counts[i['run']] for i in train]);weight/=weight.mean()
    x=torch.tensor((x-mean)/std);vx=torch.tensor((vx-mean)/std)
    y=torch.tensor(y);vy=torch.tensor(vy)
    stable=torch.tensor(stable);vstable=torch.tensor(vstable);weight=torch.tensor(weight,dtype=torch.float32)
    model=MultiRateTemperatureLSTM(mean,std)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    best=None;best_loss=float('inf');patience=0
    for epoch in range(epochs):
        model.train();order=torch.randperm(len(x))
        for start in range(0,len(x),128):
            ids=order[start:start+128];optimizer.zero_grad()
            err=model(x[ids])-y[ids]
            under=torch.where((y[ids]>.15)&(err<0),2.,1.)
            base=nn.functional.smooth_l1_loss(err,torch.zeros_like(err),beta=.2,reduction='none')+.1*err.square()
            # Penalize error against measured future targets, not a fabricated zero delta.
            stable_loss=stationary_weight*stable[ids]*err.square()
            loss=((base*under+stable_loss)*weight[ids]).mean()
            loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
        model.eval()
        with torch.inference_mode():
            err=(model(vx)-vy).abs()
            value=float(err.mean()+.1*err[vstable].mean())
        if value<best_loss:
            best={k:v.clone() for k,v in model.state_dict().items()};best_loss=value;patience=0
        else:
            patience+=1
            if patience>=12:break
    model.load_state_dict(best);model.eval()
    return model,mean,std,int(stable.sum()),epoch+1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=RESULTS_DIR/'lstm_v5')
    parser.add_argument('--epochs',type=int,default=100)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False);(args.output/'models').mkdir()
    torch.set_num_threads(1)
    data=pd.read_csv(TRAINING_CSV)
    data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda t:t.timestamp())
    manifest=pd.read_csv(SPLIT_CSV);manifest.to_csv(args.output/'run_manifest.csv',index=False)
    if set(manifest.run)!=set(data.run) or manifest.run.duplicated().any():raise ValueError('Run split mismatch')
    items=examples(data,include_raw_rates=True)
    groups={s:[i for i in items if i['run'] in set(manifest.loc[manifest.split==s,'run'])] for s in ('train','validation','test')}
    candidates=[]
    for w in (0.,1.,3.):
        model,mean,std,stable_count,epochs=fit(groups['train'],groups['validation'],w,args.epochs)
        metrics=score(groups['validation'],predictions(model,groups['validation'],mean,std))
        candidates.append(dict(weight=w,model=model,mean=mean,std=std,metrics=metrics,stable_count=stable_count,epochs=epochs))
        print('validation stationary loss',w,metrics,flush=True)
    eligible=[c for c in candidates if c['metrics']['fp']/max(c['metrics']['actual_normal'],1)<=.05]
    chosen=min(eligible or candidates,key=lambda c:(c['metrics']['fn'],c['metrics']['mae_c'],c['metrics']['fp']))
    old=torch.load(MODELS_DIR/'lstm_temperature_multirate_v4.pt',map_location='cpu',weights_only=False)
    artifact={k:v for k,v in old.items() if k!='state_dict'}
    artifact.update(state_dict=chosen['model'].state_dict(),feature_mean=chosen['mean'].tolist(),
                    feature_std=chosen['std'].tolist(),model_release='v5',stationary_loss_weight=chosen['weight'],
                    underprediction_weight=2.,control_policy='v5',training_seed=42,
                    training_csv_sha256=hashlib.sha256(TRAINING_CSV.read_bytes()).hexdigest(),
                    split_csv_sha256=hashlib.sha256(SPLIT_CSV.read_bytes()).hexdigest())
    torch.save(artifact,args.output/'models/lstm_temperature_multirate_v5.pt')
    baseline=LstmMpcController(MODELS_DIR/'lstm_temperature_multirate_v4.pt',26,35)
    rows=[];exports=[]
    for split in ('validation','test'):
        current=groups[split]
        preds={'v4':np.array([baseline.forecast(i['history'],i['intake'],i['exhaust']) for i in current]),
               'v5':predictions(chosen['model'],current,chosen['mean'],chosen['std'])}
        for name,p in preds.items():
            rows.append(dict(model=name,split=split,**score(current,p)))
            exports.extend(dict(model=name,split=split,run=i['run'],timestamp=i['timestamp'],actual=i['target'],predicted=float(v)) for i,v in zip(current,p))
    pd.DataFrame(rows).to_csv(args.output/'prediction_metrics.csv',index=False)
    pd.DataFrame(exports).to_csv(args.output/'predictions.csv',index=False)
    report=dict(selected_stationary_weight=chosen['weight'],stationary_train_examples=chosen['stable_count'],
                epochs=chosen['epochs'],split_examples={k:len(v) for k,v in groups.items()},
                candidates=[dict(stationary_weight=c['weight'],validation=c['metrics'],epochs=c['epochs']) for c in candidates],
                metrics=rows,selection='Validation FPR <=5%, then FN, MAE, FP. Test not used for selection.',
                limitation='Previously inspected development test, not independent final test. Control-policy improvements are separate from learned weights.')
    (args.output/'training_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(pd.DataFrame(rows).to_string(index=False),flush=True)


if __name__=='__main__':main()

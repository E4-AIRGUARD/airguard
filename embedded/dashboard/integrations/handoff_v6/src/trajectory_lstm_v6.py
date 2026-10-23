"""Multi-horizon thermal trajectory and upper-limit risk estimator."""
import torch
from torch import nn


class TrajectoryLSTM(nn.Module):
    def __init__(self,mean,std):
        super().__init__()
        self.register_buffer('feature_mean',torch.tensor(mean,dtype=torch.float32))
        self.register_buffer('feature_std',torch.tensor(std,dtype=torch.float32))
        self.register_buffer('horizons',torch.tensor([1.,3.,5.]))
        self.lstm=nn.LSTM(10,32,batch_first=True)
        self.temperature_head=nn.Linear(32,3)
        self.risk_head=nn.Linear(32,1)
        nn.init.zeros_(self.temperature_head.weight);nn.init.zeros_(self.temperature_head.bias)

    def details(self,inputs):
        raw=inputs*self.feature_std+self.feature_mean
        t=torch.arange(raw.shape[1],device=raw.device,dtype=raw.dtype);t=t-t.mean()
        slope=(raw[:,:,0]*t).sum(1)/t.square().sum()
        output,_=self.lstm(inputs);last=output[:,-1]
        delta=slope[:,None]*self.horizons+4*torch.tanh(self.temperature_head(last))
        tail_rate=(delta[:,2]-delta[:,1])/2
        return delta,tail_rate,self.risk_head(last).squeeze(1)

    def forward(self,inputs):
        return self.details(inputs)[0][:,2]

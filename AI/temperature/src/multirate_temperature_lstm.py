"""원본 1·3·5초 변화율을 추가한 추세 보정 LSTM입니다."""
import torch
from torch import nn


class MultiRateTemperatureLSTM(nn.Module):
    def __init__(self,mean,std,horizon=5):
        super().__init__()
        self.register_buffer('feature_mean',torch.tensor(mean,dtype=torch.float32))
        self.register_buffer('feature_std',torch.tensor(std,dtype=torch.float32))
        self.lstm=nn.LSTM(9,32,batch_first=True)
        self.head=nn.Linear(32,1)
        nn.init.zeros_(self.head.weight);nn.init.zeros_(self.head.bias)
        self.horizon=horizon

    def forward(self,inputs):
        raw=inputs*self.feature_std+self.feature_mean
        t=torch.arange(raw.shape[1],device=raw.device,dtype=raw.dtype)
        t=t-t.mean()
        slope=(raw[:,:,0]*t).sum(1)/t.square().sum()
        output,_=self.lstm(inputs)
        return self.horizon*slope+2.*torch.tanh(self.head(output[:,-1]).squeeze(1))

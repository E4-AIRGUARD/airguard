"""현재 온도에 최근 패턴으로 구한 변화량을 더하는 5초 예측 LSTM입니다."""
from __future__ import annotations

import numpy as np
import torch
from torch import nn


def relative_window(history, intake, exhaust):
    context = np.asarray(history, dtype=np.float32).copy()
    context[:, 0] -= context[-1, 0]
    return np.column_stack((context, np.tile((intake, exhaust), (len(context), 1)))).astype(np.float32)


class StableTemperatureLSTM(nn.Module):
    """절대 온도 의존성을 줄이고, 안정 상태에서 변화량이 커지는 것을 억제합니다."""

    def __init__(self, mean, std, horizon=5, gated=True):
        super().__init__()
        self.register_buffer('feature_mean', torch.tensor(mean, dtype=torch.float32))
        self.register_buffer('feature_std', torch.tensor(std, dtype=torch.float32))
        self.lstm = nn.LSTM(6, 32, batch_first=True)
        self.head = nn.Linear(32, 1)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        self.horizon = horizon
        self.gated = gated

    def forward(self, inputs):
        raw = inputs * self.feature_std + self.feature_mean
        # 윈도우 전체의 기울기는 한 표본의 온도 차보다 잡음 영향이 작습니다.
        t = torch.arange(raw.shape[1], device=raw.device, dtype=raw.dtype)
        t = t-t.mean()
        slope = (raw[:, :, 0] * t).sum(dim=1) / t.square().sum()
        output, _ = self.lstm(inputs)
        correction = 2.0 * torch.tanh(self.head(output[:, -1]).squeeze(1))
        if self.gated:
            activity = (raw[:, :, 0].amax(dim=1)-raw[:, :, 0].amin(dim=1))/0.15
            correction = correction * activity.clamp(0, 1)
        return self.horizon*slope + correction

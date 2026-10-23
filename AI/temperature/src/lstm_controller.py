"""배포된 v6 모델만 로드합니다. 학습·이전 모델 호환 코드는 포함하지 않습니다."""
import hashlib
from pathlib import Path

import numpy as np
import torch

from trajectory_lstm_v6 import TrajectoryLSTM
from trajectory_control import choose_trajectory

MODEL_SHA256 = 'ed0adbdb8ee35a3703bfc0bba4cd1d441e2c454233f6b3ff5e14ff516ee418f6'
PWM_LEVELS = (35, 60, 100)
PWM_CANDIDATES = [(intake, exhaust) for intake in PWM_LEVELS for exhaust in PWM_LEVELS]


class LstmController:
    def __init__(self, model_path: Path, target_c=26.0):
        self.model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if self.model_sha256 != MODEL_SHA256:
            raise ValueError('전달용 v6 모델 해시와 다릅니다. 모델·코드 버전을 확인하세요.')
        artifact = torch.load(model_path, map_location='cpu', weights_only=False)
        if (artifact.get('architecture') != 'trajectory_relative_lstm'
                or artifact.get('sequence_length') != 8 or artifact.get('horizon_seconds') != 5
                or artifact.get('context_feature_count') != 8):
            raise ValueError('전달용 v6 입력 규격이 아닙니다.')
        self.mean = np.asarray(artifact['feature_mean'], dtype=np.float32)
        self.std = np.asarray(artifact['feature_std'], dtype=np.float32)
        self.model = TrajectoryLSTM(self.mean, self.std)
        self.model.load_state_dict(artifact['state_dict'])
        self.model.eval()
        self.model_path = str(model_path.resolve())
        self.sequence_length = 8
        self.horizon_seconds = 5
        self.target_c = target_c
        self.prediction_margin = np.array(artifact['prediction_margin_c'])
        self.risk_threshold = artifact['risk_threshold']
        self.rate_weight = artifact['rate_weight']
        self.upper_risk_count = 0
        self.pwm_candidates = list(PWM_CANDIDATES)
        self.current_command_pair = None
        self.display_name = 'LSTM v6 (5s)'

    def trajectory(self, history, intake, exhaust):
        context = history[-8:].copy()
        window = np.column_stack((context, np.tile((intake, exhaust), (len(context), 1))))
        window[:, 0] -= window[-1, 0]
        normalized = (window - self.mean) / self.std
        with torch.inference_mode():
            # 일부 JetPack 빌드의 NumPy-Torch 변환 문제를 피합니다.
            delta, rate, logit = self.model.details(torch.tensor(normalized.tolist(), dtype=torch.float32)[None])
        temperatures = float(context[-1, 0]) + np.array(delta[0].tolist())
        rates = np.diff(np.r_[float(context[-1, 0]), temperatures]) / np.array([1., 2., 2.])
        return dict(temperatures=temperatures, rate=float(rate.item()), max_rate=float(np.max(rates)),
                    risk_score=float(torch.sigmoid(logit).item()))

    def forecast_current(self, history):
        return float(self.trajectory(history, int(history[-1, 2]), int(history[-1, 3]))['temperatures'][-1])

    def upper_risk(self, history, temperature):
        detail = self.trajectory(history, int(history[-1, 2]), int(history[-1, 3]))
        risky = np.max(detail['temperatures'] + self.prediction_margin) >= self.target_c or (
            detail['risk_score'] >= self.risk_threshold and history[-1, 4] > .03)
        self.upper_risk_count = self.upper_risk_count + 1 if risky else 0
        return temperature >= self.target_c or (risky and history[-1, 4] >= .08) or self.upper_risk_count >= 2

    def choose(self, history):
        return choose_trajectory(self, history[-8:], self.pwm_candidates)

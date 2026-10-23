"""저장소 폴더 위치(이 파일이 src/ 안에 있다고 가정)."""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
MODELS = os.path.join(ROOT, 'models')
DATA = os.path.join(ROOT, 'data')
RESULTS = os.path.join(ROOT, 'benchmarks', 'results')

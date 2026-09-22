# vision_qc — 부품 인식 1차 시험 + YOLO 검출 파이프라인

미니블록(BLOCKING) 부품을 웹캠으로 검사하는 두 단계.

1. **1차 시험** (`part_feature_test.py`) — 후보 부품들이 촬영 거리에서
   실제로 구분되는 특징(길이 차이·돌기·구멍·외곽선)을 보여주는지 눈으로
   먼저 확인한다.
2. **YOLO 검출 파이프라인** (`build_yolo_dataset.py` → `train_yolo.py` →
   `verify_kit_live.py`) — 1차 시험에서 특징이 잘 보인 부품들로 실제 검출
   모델을 학습하고, 작업 A/B 준비 목록과 실시간으로 대조한다.

## 준비

```bash
pip install -r requirements.txt
```

- `ultralytics`는 설치 시 `torch`를 함께 받는다 — 다운로드 용량이 크고(수백MB~1GB대)
  네트워크 상태에 따라 시간이 걸린다.
- 기본 pip 설치는 CPU 전용 torch를 받는다. 이 PC에 NVIDIA GPU가 있고 학습을
  빠르게 하고 싶다면, `ultralytics` 설치 전에 [pytorch.org](https://pytorch.org/get-started/locally/)
  안내대로 CUDA 버전 torch를 먼저 설치한다.
- Innomaker U20CAM-720p 웹캠을 USB로 연결한다.

## 1차 시험 실행

```bash
python part_feature_test.py --camera-index 1
```

카메라 인덱스가 다르면 `--camera-index 0` 등으로 바꿔본다.

### 자동 연속 캡처 (데이터셋용 다량 촬영)

매번 `c`를 누르기 번거로우면 일정 간격으로 자동 캡처할 수 있다:

```bash
python part_feature_test.py --auto-count 30 --auto-interval 4
```

4초 간격으로 30장을 자동 캡처한다. 간격 동안 부품 배치·방향·카메라 각도를
바꿀 시간을 벌 수 있다. 화면에 남은 시간과 진행 개수가 표시되고, 캡처
순간 알림음이 난다 (`--no-beep`으로 끌 수 있음). `q`로 중간에 멈출 수 있고,
자동 캡처가 끝난 뒤에도 `c`로 수동 캡처를 이어갈 수 있다.

### 절차

1. 후보 부품을 서로 떨어뜨려 무광 배경에 놓는다. (코드 밖 준비 작업)
2. `c` — 화면 전체를 캡처해 `captures/full/`에 저장한다.
3. `r` — 부품 2개를 순서대로 마우스 드래그로 선택(ROI)하고, 각각 이름/색상,
   화면 위치(중앙/가장자리), 방향(0/90/180/270도)을 입력한다.
   `captures/pairs/<타임스탬프>/`에 크롭 2장과 `compare.jpg`(나란히 비교)가 저장된다.
4. 저장된 `compare.jpg`를 보고 두 부품의 구분 특징이 실제로 보이는지 눈으로 판단한다.
   `e`를 눌러 윤곽선(Canny) 오버레이를 켜면 외곽선이 뭉개지는지 미리 볼 수 있다.
5. 같은 부품 쌍을 화면 중앙/가장자리, 방향을 바꿔가며 여러 번 `r`로 반복 캡처하고
   `compare.jpg`들을 비교해 조건이 바뀌어도 구분되는지 확인한다.

특징이 뭉개지면: 검사 영역을 좁히거나, 작은 부품 구역만 크게 잡는 두 번째
웹캠 구도를 검토한다. 단순 디지털 확대로는 새로운 디테일이 생기지 않는다.

`q`로 종료.

## YOLO 검출 파이프라인

1차 시험에서 구분이 잘 되는 걸 확인한 부품 5~8종으로 진행한다.

### 1) 학습용 이미지 모으기

`part_feature_test.py`의 `c` 키로 부품 배치를 바꿔가며(개수·위치·방향·조명)
`captures/full/`에 여러 장 모은다. 클래스당 최소 20~30장, 다양한 배치를 권장한다.

### 2) labelme로 라벨링

```bash
labelme captures/full --output labelme_annotations --nodata
```

각 이미지에서 부품마다 사각형(rectangle)을 그리고, 라벨 이름을 부품 종류로
입력한다 (예: `white_short`, `white_long`, `gray_common`). 이 이름이 곧 YOLO
클래스 이름이 되고, `kit_config.yaml`의 키와 반드시 일치해야 한다.

### 3) YOLO 데이터셋으로 변환

```bash
python build_yolo_dataset.py --images captures/full --labels labelme_annotations --out dataset
```

`dataset/images/{train,val}`, `dataset/labels/{train,val}`, `dataset/data.yaml`이 생성된다.

### 4) 학습

```bash
# 먼저 짧게 돌려서 파이프라인이 끝까지 도는지 확인
python train_yolo.py --data dataset/data.yaml --epochs 20 --imgsz 640

# 문제 없으면 본 학습
python train_yolo.py --data dataset/data.yaml --epochs 100 --imgsz 1280
```

결과 가중치는 `runs/detect/train/weights/best.pt`에 생긴다.

### 5) 준비 목록 대조 (실시간)

`kit_config.yaml`을 실제 부품 이름/수량으로 수정한 뒤:

```bash
python verify_kit_live.py --weights runs/detect/train/weights/best.pt --task A
```

웹캠 화면에 검출 박스가 표시되고, `space`를 누르면 현재 프레임의 부품 개수를
`kit_config.yaml`의 작업별 준비 목록과 대조해 부족/초과를 터미널에 출력한다.
`q`로 종료.

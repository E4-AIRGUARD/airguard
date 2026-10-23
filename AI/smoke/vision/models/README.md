# models

| 파일 | 설명 |
|---|---|
| `smoke_cls_v4.pt` | 최종 모델 (yolo11n-cls, 클래스: `no_smoke`, `smoke`, 약 3.2MB) |

- md5: `e0da65adb4a9c51057a35a1ff306b791` (`md5sum smoke_cls_v4.pt`로 확인)
- 학습: `training/train_colab_cls_v4.py` (Colab T4, 80 epoch, 설정 `C_n_strongaug`)
- 추천 켜기 기준: 0.40 (val F1 최적). 더 민감하게 하려면 0.30~0.35 (`docs/optimization_summary.md` 참고)

## TensorRT 변환 (Jetson에서)
`.engine`은 변환한 기기와 TensorRT 버전에 묶여 있어서 git에 올리지 않습니다(`.gitignore`로 제외). 각 기기에서 직접 변환합니다 (Jetson Orin Nano에서 약 270초).
```
yolo export model=models/smoke_cls_v4.pt format=engine half=True imgsz=640
```
`.engine`을 쓸 때는 코드에서 `YOLO(path, task='classify')`로 불러야 합니다 (이 폴더의 스크립트는 이미 그렇게 되어 있습니다).

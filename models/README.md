# 탐지 모델

불·연기 탐지 YOLOv8 모델. [AlimTleuliyev/wildfire-detection](https://github.com/AlimTleuliyev/wildfire-detection) (MIT)이 D-Fire 데이터셋(약 2만 1천 장)으로 학습한 것을 그대로 쓴다. 파일이 커서 git에 넣지 않으므로 처음 한 번 받는다 (저장소 루트에서):

```powershell
curl.exe -fL -o models/fire_n.pt https://raw.githubusercontent.com/AlimTleuliyev/wildfire-detection/main/fire-models/fire_n.pt
curl.exe -fL -o models/fire_s.pt https://raw.githubusercontent.com/AlimTleuliyev/wildfire-detection/main/fire-models/fire_s.pt
```

| 파일 | 크기 | 쓰는 곳 | mAP@50 (D-Fire) | 이 PC CPU (i5-10500) |
|---|---|---|---|---|
| `fire_n.pt` | 6 MB | 드론 엣지 AI (라즈베리파이) | 0.787 | 35 ms/장 |
| `fire_s.pt` | 22 MB | 서버 재확인 (예정) | 0.798 | 64 ms/장 |

- 클래스: 0 = `smoke`, 1 = `fire` (코드는 번호가 아니라 이름으로 연결한다).
- 옛 Ultralytics(8.0)로 저장된 파일이라 `ultralytics.yolo.utils` 같은 옛 모듈 이름이 들어 있지만, 최신 Ultralytics(8.4)가 그대로 읽는다.
- `.pt`는 pickle이라 불러올 때 코드가 실행될 수 있다. 받은 파일이 참조하는 모듈이 torch·ultralytics뿐인지 확인했다 (2026-11-02).

## 라즈베리파이용으로 바꾸기

라즈베리파이 5에서는 NCNN 형식이 가장 빠르다. 라즈베리파이에서 한 번 실행:

```bash
yolo export model=models/fire_n.pt format=ncnn imgsz=640   # → models/fire_n_ncnn_model/
```

## 시뮬레이션에서 잰 탐지율 (참고값)

`python sim/evaluate_detection.py`로 잰다. 가상 화재 3곳(지름 3~6 m) 바로 위, 고도 30·40 m, 방향 12가지 = 72장 기준 (신뢰도 0.4 이상):

| 모델 | 카메라 바로 아래 | 앞으로 30° | 앞으로 45° |
|---|---|---|---|
| fire_n | 46% | 47% | 29% |
| fire_s | 42% | 36% | — |

- 한 장 기준이다. 실제 비행에서는 화재가 화면에 3~4초(약 10장) 보이므로, SITL 시험 비행에서는 지나간 화재 3곳을 모두 잡았다.
- 연기가 화면 아래쪽으로 흐르는 방향에서는 잘 못 잡는다. 학습 데이터가 대부분 옆에서 찍은 사진(연기가 위로 올라감)이라서로 보인다. 드론 시점(위에서 내려다본) 사진으로 추가 학습하면 나아질 것이다.
- 가상 화재는 실제 불과 모양이 다르므로 실제 영상으로 다시 확인해야 한다.

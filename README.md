# ml-project

Python 머신러닝/AI 프로젝트.

## 구조
- `data/` — 데이터셋 (git 미추적)
- `notebooks/` — 실험용 Jupyter 노트북
- `src/` — 소스 코드 (`src/models/` 모델 정의 등)
- `main.py` — 진입점

## 시작하기
```bash
uv sync            # 의존성 설치
uv run main.py     # 실행
uv run jupyter lab # 노트북
```

## 패키지 추가
```bash
uv add <패키지명>
```

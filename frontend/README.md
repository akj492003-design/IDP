# IDP System — Frontend

React 19 + Vite 8 frontend for the IDP System. The Flask API and Celery services are in the sibling `backend/` directory.

## Requirements

- Node.js 22.12+ (Vite 8)
- npm 10+
- Backend services started with Docker Compose (MongoDB, Redis, Flask API and Celery worker)

## Start the backend

Open PowerShell in `backend`, create `.env` from `.env.example`, configure a unique `SECRET_KEY` and required MongoDB settings, then run:

```powershell
Copy-Item .env.example .env -ErrorAction SilentlyContinue
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:5000/api/health
```

The health endpoint should report MongoDB and Redis as `ok`. Check worker startup with:

```powershell
docker compose logs --tail=100 api worker
docker compose exec worker celery -A run.celery_app inspect ping
```

## Start the frontend

From `frontend`:

```powershell
npm ci
npm run dev
```

The default API URL is `http://localhost:5000/api`.

## Evaluation dashboard

The model-performance page reads `/api/classification/models`, which serves `backend/app/data/model_metrics.json`.

- Stage 9 displays the seven candidate models' development OOF metrics across 6,387 records and five folds.
- Stage 10 displays the final EfficientNetB0 held-out evaluation across 1,127 independent test records, with the confusion matrix and per-class classification report.
- These are distinct evaluation sets and are labelled separately.
- The seven-model inference benchmark currently displays **Benchmark data unavailable** because only the Stage 11 EfficientNetB0 deployable package was provided.

## Tailwind CSS v4

The existing navy-blue utility-class design is retained. No custom stylesheet or `index.css` has been added.

## Authentication and protected pages

`/login`, `/signup` and password-reset routes are public. Home, Scanning, History, Classification, Track & Manage, Settings, Subscription, Checkout, Payment, Scanning Result and Model Performance require a valid Flask session.

A `401` from `GET /api/auth/me` before login is expected. A `401` from a protected endpoint after login requires checking the session cookie and API origin configuration.

## Checks

```powershell
npm ci
npm run lint
npm run build
```

Backend tests from `backend`:

```powershell
python -m compileall -q app run.py
pytest -q
```

## Operational boundaries

Stage 11 EfficientNetB0 requires Stage 4 canonical grayscale page images. File types that do not produce those images are not silently sent through the retired SVM/Paddle classification paths. OCR and structured field extraction remain separate from classifier benchmark accuracy.

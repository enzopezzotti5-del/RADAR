# Compatibility Candidate

Candidate frontend tag proposal: `frontend-radar-react-candidate-2026-07-30`.
Candidate backend tag proposal: `backend-radar-v2-candidate-2026-07-30`.

This frontend must use `VITE_RADAR_READ_ONLY=true` and the relative API base
`/api`. It expects the canonical backend on port `5000`; the planned static
frontend endpoint is port `8081`.

The compatibility contract includes `GET /api/calendar/summary`, the backend
SQLite tables `run_metric_items` and `run_metrics`, and timezone
`America/Sao_Paulo`. The backend requires `RADAR_V2_SECRET_KEY` in production.

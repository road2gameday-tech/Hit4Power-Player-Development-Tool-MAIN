# Hit4Power Player Development Tool (FastAPI)

One-click deploy on Render using `render.yaml`. Player dashboards with Exit Velocity chart, coach notes, and assigned drills. Instructor view with Clients grouped by age, favorites ("My Clients"), CSV bulk import, player creation with generated login codes, SMS texting (optional via Twilio), drill library (instructor-only), and player image uploads.

## Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY=dev
uvicorn app.main:app --reload
```

Visit http://localhost:8000

## Environment variables

- `SECRET_KEY` (required): session signing
- `DATABASE_URL` (optional): default sqlite:///app.db. On Render set to `sqlite:////var/data/app.db` (provided in blueprint).
- `INSTRUCTOR_DEFAULT_CODE` (optional): created on first run if no instructors exist
- Twilio (optional to enable texting):
  - `TWILIO_ACCOUNT_SID`
  - `TWILIO_AUTH_TOKEN`
  - `TWILIO_FROM_NUMBER`
- `BASE_URL` (optional): included in SMS links

## Render deploy

Create a new Blueprint and point it at this repo. The blueprint provisions a disk and sets `DATABASE_URL` to persist your data.


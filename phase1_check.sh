# Phase 1 Verification Commands

## 1. Project Structure
ls com_website/*.html com_website/*.js com_website/*.css
ls desktop/main.js desktop/preload.js desktop/package.json
ls backend/main.py backend/config.py backend/pyproject.toml backend/requirements.txt backend/.env.example
ls backend/api/*.py backend/db/*.py backend/db/migrations/versions/*.py
ls backend/agents/scout*.py backend/agents/scout_scraper.mjs backend/agents/package.json
ls docker/docker-compose.yml
ls sage_wiki/SCHEMA.md sage_wiki/wiki/index.md sage_wiki/wiki/log.md
ls .gitignore start.py

## 2. Docker Services (start + verify)
docker compose -f docker/docker-compose.yml up -d --wait
docker compose -f docker/docker-compose.yml ps
# Wait for health checks:
curl -s http://localhost:5432 >/dev/null && echo "postgres reachable" || echo "postgres DOWN"
redis-cli ping 2>/dev/null && echo "redis OK" || echo "redis DOWN"
curl -s http://localhost:7474 >/dev/null && echo "neo4j reachable" || echo "neo4j DOWN"
curl -s http://localhost:8000/api/v1/heartbeat && echo "chromadb OK" || echo "chromadb DOWN"

## 3. Python Environment
source .venv/bin/activate && pip list 2>/dev/null | grep -cE "fastapi|sqlalchemy|alembic|pgvector|pydantic"
# Should return >= 5

## 4. Database Migration
source .venv/bin/activate && cd backend && alembic upgrade head
# Should output: INFO  [alembic.runtime.migration] Running upgrade -> aeb264f6ae36, initial_schema

## 5. Backend API
source .venv/bin/activate && cd backend && cp -n .env.example .env && uvicorn main:app --host 0.0.0.0 --port 8734 &
sleep 3
curl -s http://localhost:8734/api/health | python -m json.tool
curl -s http://localhost:8734/api/health/ready | python -m json.tool
# Sessions CRUD:
curl -s -X POST http://localhost:8734/api/sessions -H 'Content-Type: application/json' -d '{"title":"Test Session"}' | python -m json.tool
curl -s http://localhost:8734/api/sessions | python -m json.tool
# Documents:
curl -s http://localhost:8734/api/documents | python -m json.tool

## 6. Electron / Node.js
cd desktop && npx electron --version
# Check dependencies installed:
ls desktop/node_modules/electron-store/package.json && echo "electron-store OK" || echo "electron-store MISSING"
ls desktop/node_modules/electron/package.json && echo "electron OK" || echo "electron MISSING"
# Scout scraper deps:
ls backend/agents/node_modules/@lightpanda/browser/package.json && echo "lightpanda OK" || echo "MISSING"
ls backend/agents/node_modules/puppeteer-core/package.json && echo "puppeteer-core OK" || echo "MISSING"

## 7. Frontend JS (syntax check)
node --check com_website/api.js && echo "api.js valid"
node --check com_website/ws.js && echo "ws.js valid"

## 8. Cleanup
# Kill uvicorn:
kill $(lsof -ti:8734) 2>/dev/null
# Stop Docker services:
docker compose -f docker/docker-compose.yml down

# Phase 2 Verification Commands

## 1. Structure — agent modules
ls backend/agents/schemas.py backend/agents/prompts.py backend/agents/graph.py
ls backend/agents/router.py backend/agents/historian.py backend/agents/detective.py
ls backend/agents/sage.py backend/agents/sage_wiki.py backend/agents/moderator.py backend/agents/companion.py

## 2. Structure — API routers
ls backend/api/deliberations.py backend/api/verdicts.py backend/api/websocket.py backend/api/agents.py

## 3. Structure — LLM config
grep LLM_API_BASE backend/.env && echo "LM Studio configured" || echo "MISSING"

## 4. Python imports (no Docker needed)
source .venv/bin/activate && cd backend && python -c "
from agents.schemas import ScoutResponse, HistorianResponse, DetectiveResponse, SageResponse, ModeratorResponse, CompanionResponse
from agents.prompts import AGENT_CONFIGS
from agents.router import router_node
from agents.historian import historian_node
from agents.detective import detective_node
from agents.sage import sage_node
from agents.moderator import moderator_node
from agents.companion import companion_node
from agents.graph import get_graph
print('All imports OK')
print('Graph nodes:', len(get_graph().nodes))
"

## 5. Docker services
docker compose -f docker/docker-compose.yml up -d --wait
docker compose -f docker/docker-compose.yml ps

## 6. Database migration (if fresh)
source .venv/bin/activate && cd backend && alembic upgrade head

## 7. Start backend
source .venv/bin/activate && cd backend && uvicorn main:app --host 0.0.0.0 --port 8734 &
sleep 3

## 8. API endpoints — health + agents
curl -s http://localhost:8734/api/health | python -m json.tool
curl -s http://localhost:8734/api/agents | python -m json.tool

## 9. API — create session + start deliberation
SESSION=$(curl -s -X POST http://localhost:8734/api/sessions -H 'Content-Type: application/json' -d '{"title":"Phase 2 LM Studio Test"}')
SID=$(echo "$SESSION" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Session: $SID"

DELIB=$(curl -s -X POST "http://localhost:8734/api/sessions/$SID/deliberate" \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the current state of local LLM inference?"}')
DID=$(echo "$DELIB" | python -c "import sys,json; print(json.load(sys.stdin)['deliberation_id'])")
echo "Deliberation: $DID"

## 10. Poll status (every 5s until completed)
for i in $(seq 1 20); do
  STATUS=$(curl -s "http://localhost:8734/api/deliberations/$DID/status" | python -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "[$i] $STATUS"
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then break; fi
  sleep 5
done

## 11. Fetch full deliberation with agent responses
curl -s "http://localhost:8734/api/deliberations/$DID" | python -m json.tool | head -60

## 12. Verdict — submit
curl -s -X POST http://localhost:8734/api/verdicts \
  -H 'Content-Type: application/json' \
  -d "{\"deliberation_id\":\"$DID\", \"chosen_agent\":\"scout\"}" | python -m json.tool

## 13. Verdict — get
curl -s "http://localhost:8734/api/verdicts/$DID" | python -m json.tool

## 14. WebSocket test (python one-liner)
python -c "
import asyncio, json, websockets
async def test():
    async with websockets.connect('ws://localhost:8734/ws/sessions/test/stream') as ws:
        await ws.send(json.dumps({'type':'query','content':'Hello world'}))
        for _ in range(3):
            msg = await asyncio.wait_for(ws.recv(), timeout=30)
            data = json.loads(msg)
            print(f'WS: {data[\"type\"]}')
asyncio.run(test())
"

## 15. Cleanup
kill $(lsof -ti:8734) 2>/dev/null
docker compose -f docker/docker-compose.yml down

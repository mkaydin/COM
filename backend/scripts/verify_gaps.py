"""Self-contained deliberation test — starts server, runs query, verifies 5 gaps."""
import sys
sys.path.insert(0, "/home/mka/Code/COM/backend")

import asyncio
import time
import json
import threading

def start_server():
    import uvicorn
    config = uvicorn.Config("main:app", host="127.0.0.1", port=8734, log_level="error")
    server = uvicorn.Server(config)
    server.run()

def log(msg):
    print(msg, flush=True)

async def run_test():
    import httpx

    # Wait for server
    for _ in range(10):
        try:
            r = await asyncio.to_thread(httpx.get, "http://127.0.0.1:8734/api/health", timeout=5)
            if r.status_code == 200:
                break
        except:
            pass
        await asyncio.sleep(1)
    else:
        log("ERROR: Server didn't start")
        return

    log("Server up")

    QUERY = "What is the relationship between infill density and compressive strength in 3D printed structures?"

    # Create session
    r = await asyncio.to_thread(
        httpx.post, "http://127.0.0.1:8734/api/sessions",
        json={"title": "Gap Fix Final"}, timeout=10
    )
    session_id = r.json()["id"]
    log(f"Session: {session_id[:8]}")

    # Start deliberation
    r = await asyncio.to_thread(
        httpx.post, f"http://127.0.0.1:8734/api/sessions/{session_id}/deliberate",
        json={"query": QUERY}, timeout=30
    )
    deliberation_id = r.json()["deliberation_id"]
    log(f"Deliberation: {deliberation_id[:8]}")

    # Poll
    for i in range(40):
        await asyncio.sleep(10)
        r = await asyncio.to_thread(
            httpx.get, f"http://127.0.0.1:8734/api/deliberations/{deliberation_id}/status",
            timeout=10
        )
        status_data = r.json()
        s = f"{status_data['status']} ({status_data['agent_count']}a)"
        log(f"  [{(i+1)*10}s] {s}")
        if status_data["status"] in ("completed", "failed"):
            break

    # Fetch result
    r = await asyncio.to_thread(
        httpx.get, f"http://127.0.0.1:8734/api/deliberations/{deliberation_id}",
        timeout=10
    )
    d = r.json()

    log(f"\n{'='*55}")
    log(f"Status: {d['status']} | Type: {d.get('query_type')} | Responses: {len(d['responses'])}")
    log(f"{'='*55}\n")

    responses = {x['agent']: x.get('response', {}) for x in d['responses']}

    # GAP 1: Historian citations
    hist = responses.get('historian', {})
    claims = hist.get('claims', [])
    cited = sum(1 for c in claims if c.get('sources'))
    log(f"GAP 1 (Historian Citations): {cited}/{len(claims)} claims have citations")
    for c in claims[:2]:
        src_titles = [s.get('title','?')[:40] if isinstance(s,dict) else str(s)[:40] for s in c.get('sources',[])]
        log(f"  -> {c['text'][:65]}...")
        log(f"     sources: {src_titles}")
    g1 = cited > 0

    # GAP 2: Detective graph
    det = responses.get('detective', {})
    incons = det.get('inconsistencies', [])
    chains = det.get('causal_chains', [])
    graph_ok = not any('empty' in str(i).lower() or 'unreachable' in str(i).lower() for i in incons)
    log(f"\nGAP 2 (Detective Graph): conf={det.get('confidence')}, {len(chains)} chains, graph_used={graph_ok}")
    for c in chains[:2]:
        log(f"  -> {' -> '.join(c.get('chain',[]))}")
    g2 = graph_ok

    # GAP 3: Scout provenance
    scout = responses.get('scout', {})
    rel = scout.get('source_reliability', '')
    sig_count = len(scout.get('key_signals', []))
    log(f"\nGAP 3 (Scout Provenance): conf={scout.get('confidence')}, reliability='{rel}', signals={sig_count}")
    log(f"  consensus: {str(scout.get('consensus',''))[:80]}...")
    g3 = scout.get('confidence', 0) > 0

    # GAP 4: Moderator schema
    mod = responses.get('moderator', {})
    log(f"\nGAP 4 (Moderator Schema): conflicts={len(mod.get('conflicts',[]))}, agreements={len(mod.get('agreements',[]))}, decisions={len(mod.get('decision_space',[]))}")
    for ds in mod.get('decision_space', []):
        log(f"  -> {ds.get('label','')}: {ds.get('action','')[:50]}...")
    g4 = len(mod.get('decision_space',[])) >= 3

    # GAP 5: Companion guidance
    comp = responses.get('companion', {})
    g_list = comp.get('trust_guidance', [])
    named = [x.get('agent','') for x in g_list]
    log(f"\nGAP 5 (Companion Guidance): {len(g_list)} entries, agents={named}")
    for x in g_list:
        log(f"  -> [{x.get('trust_level','')}] {x.get('agent','?')}: {x.get('reason','')[:50]}...")
    g5 = len(g_list) >= 4 and all(x.get('agent') for x in g_list)

    # Summary
    results = {
        "G1 Historian citations": g1,
        "G2 Detective graph": g2,
        "G3 Scout provenance": g3,
        "G4 Moderator schema": g4,
        "G5 Companion guidance": g5,
    }
    log(f"\n{'='*55}")
    for name, ok in results.items():
        log(f"  {'PASS' if ok else 'FAIL'}  {name}")
    score = sum(results.values())
    log(f"\n  {score}/5 gaps fixed and verified")
    log(f"{'='*55}")

    # Save result
    with open("/tmp/final_verify_result.json", "w") as f:
        json.dump(d, f, indent=2, default=str)
    log("\nFull result saved to /tmp/final_verify_result.json")


def main():
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(2)
    asyncio.run(run_test())


if __name__ == "__main__":
    main()

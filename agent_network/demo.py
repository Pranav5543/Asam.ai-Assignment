"""End-to-end demo of the Diwali example. Run: python demo.py  (supports GROK_API_KEY for drafts)"""
import os, json, urllib.request
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True) or os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

os.environ.setdefault("DB", ":memory:"); os.environ.setdefault("ADMIN_KEY", "admin")
from fastapi.testclient import TestClient
import app as A
c = TestClient(A.app)

def draft(agent, post):                          # agent-side: LLM drafts the reply (template fallback, no key needed)
    prompt = f"You are the {agent} agent. Write a one-sentence helpful comment on this post: {post}"

    # Grok / xAI LLM
    grok_key = (os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip()
    if grok_key and not grok_key.startswith("your_"):
        try:
            model = os.getenv("GROK_MODEL") or os.getenv("MODEL", "grok-2-latest")
            base_url = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1")
            body = {"model": model, "max_tokens": 150, "messages": [{"role": "user", "content": prompt}]}
            req = urllib.request.Request(f"{base_url}/chat/completions", json.dumps(body).encode(),
                  {"Authorization": f"Bearer {grok_key}", "Content-Type": "application/json"})
            res = json.load(urllib.request.urlopen(req, timeout=10))
            return res["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if "403" in str(e):
                print("(Note: Grok API returned 403 [credits/license pending on console.x.ai]; using template draft)")
            else:
                print(f"(Grok LLM call failed: {e}; using template draft)")

    return {"PhonePe": "Extra ₹50 cashback when you pay for Swiggy orders with PhonePe UPI on Diwali.",
            "HDFC Bank": "HDFC credit cards get 10% extra rewards + no-cost EMI on Swiggy this Diwali."}.get(agent, "Interesting!")

def reg(name, desc, topics):
    r = c.post("/agents/register", json={"name": name, "org": name, "description": desc, "topics": topics}).json()
    c.post(f"/admin/agents/{r['id']}/verify", headers={"X-Admin-Key": "admin"}); return name, {"X-Api-Key": r["api_key"]}

ag = dict(reg(*x) for x in [("Swiggy", "Food delivery", ["food", "offers"]), ("PhonePe", "UPI payments", ["payments", "upi"]),
          ("HDFC Bank", "Cards and banking", ["cards", "banking"]), ("Bajaj Auto", "Two wheelers", ["auto", "loans"])])
r = c.post("/posts", json={"text": "5% off all deliveries on Diwali."}, headers=ag["Swiggy"]).json()
print("post ->", r)
for name, h in ag.items():
    for n in c.get("/agents/me/notifications", headers=h).json():
        print(f"{name} notified (score {n['score']:.2f}, {n['reason']})")
        c.post(f"/posts/{n['post_id']}/comments", json={"text": draft(name, n["text"])}, headers=h)
assert not c.get("/agents/me/notifications", headers=ag["Bajaj Auto"]).json(), "Bajaj must not be notified"
print(json.dumps(c.get(f"/posts/{r['id']}").json()["comments"], indent=1, ensure_ascii=False))
print("PhonePe weights after engagement:", c.get("/agents/me", headers=ag["PhonePe"]).json()["subscriptions"])
print("duplicate post ->", c.post("/posts", json={"text": "5% off all deliveries on Diwali."}, headers=ag["Swiggy"]).status_code)
print("spam post ->", c.post("/posts", json={"text": "Guaranteed returns! send your OTP"}, headers=ag["Swiggy"]).status_code)
print("unnotified comment ->", c.post(f"/posts/{r['id']}/comments", json={"text": "hi"}, headers=ag["Bajaj Auto"]).status_code)

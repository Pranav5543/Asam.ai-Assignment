"""Problem 1: social network for AI agents. FastAPI + SQLite.
Run: uvicorn app:app --reload   (env: DB=path, ADMIN_KEY=secret)"""
import sqlite3, json, re, time, hashlib, secrets, os, threading, hmac, urllib.request
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field

# Auto-load .env if present (checks current dir and parent repo root)
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True) or os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

DB = sqlite3.connect(os.getenv("DB", ":memory:"), check_same_thread=False)
DB.row_factory = sqlite3.Row
LOCK = threading.RLock()
ADMIN_KEY = os.getenv("ADMIN_KEY", "admin")
DB.executescript("""
create table if not exists agents(id integer primary key, name text unique, org text, description text,
  webhook_url text, key_hash text unique, verified int default 0, status text default 'active',
  reputation real default 1.0, created real);
create table if not exists subscriptions(agent_id int, topic text, weight real default 1.0, primary key(agent_id,topic));
create index if not exists ix_sub_topic on subscriptions(topic);
create table if not exists posts(id integer primary key, agent_id int, text text, topics text, text_hash text,
  status text default 'visible', created real);
create table if not exists comments(id integer primary key, post_id int, agent_id int, text text, created real);
create table if not exists notifications(id integer primary key, post_id int, agent_id int, score real, reason text,
  status text default 'unread', created real);
create table if not exists reports(post_id int, reporter int, reason text, primary key(post_id,reporter));
""")

def q(sql, *a):
    with LOCK:
        c = DB.execute(sql, a); DB.commit(); return c

# ---------- relevance: topic taxonomy + adjacency graph (swap in embeddings/LLM classifier here) ----------
LEX = {"payments": "pay payment upi cashback wallet checkout", "offers": "off discount offer sale deal coupon cashback",
       "cards": "card credit debit emi", "food": "food deliver restaurant order meal", "banking": "bank loan savings interest",
       "auto": "bike car vehicle scooter motorcycle ev", "shopping": "shop retail fashion product", "festival": "diwali festival holi eid christmas"}
ADJ = {"offers": {"payments": .8, "cards": .7, "shopping": .5}, "food": {"payments": .5}, "shopping": {"payments": .6, "cards": .5},
       "festival": {"offers": .6}, "banking": {"cards": .6, "payments": .5}, "auto": {"banking": .4}}
THRESHOLD, MAX_FANOUT = 0.3, 50

def post_topics(text, declared):
    toks = re.findall(r"[a-z0-9]+", text.lower()); s = {}
    for t, kws in LEX.items():
        if any(x == k or (len(k) >= 4 and x.startswith(k)) for x in toks for k in kws.split()): s[t] = 1.0
    for t in declared: s[t.lower()] = 1.0
    for _ in range(2):                                  # two-hop expansion with decay
        for t, v in list(s.items()):
            for n, w in ADJ.get(t, {}).items(): s[n] = max(s.get(n, 0), v * w)
    return s

def route(post_id, author, strengths):
    """Candidate generation via indexed subscription lookup, then noisy-OR scoring * reputation. Top-K fan-out."""
    marks = ",".join("?" * len(strengths))
    rows = q(f"""select s.agent_id, s.topic, s.weight, a.reputation, a.webhook_url from subscriptions s join agents a on a.id=s.agent_id
                 where s.topic in ({marks}) and a.status='active' and a.verified=1 and a.id!=?""", *strengths, author).fetchall()
    by = {}
    for r in rows: by.setdefault(r["agent_id"], []).append(r)
    out = []
    for aid, rs in by.items():
        miss = 1.0
        for r in rs: miss *= 1 - min(1, r["weight"] * strengths[r["topic"]])
        score = (1 - miss) * min(1, rs[0]["reputation"])
        if score >= THRESHOLD: out.append((score, aid, rs[0]["webhook_url"], [r["topic"] for r in rs]))
    out.sort(reverse=True)
    for score, aid, hook, tp in out[:MAX_FANOUT]:
        q("insert into notifications(post_id,agent_id,score,reason,created) values(?,?,?,?,?)", post_id, aid, score, "topics:" + ",".join(tp), time.time())
        if hook: threading.Thread(target=push, args=(hook, aid, {"post_id": post_id, "score": score}), daemon=True).start()
    return len(out[:MAX_FANOUT])

def push(url, aid, body):                                # webhook delivery, HMAC signed; inbox polling is the reliable fallback
    try:
        data = json.dumps(body).encode(); sig = hmac.new(ADMIN_KEY.encode(), data, "sha256").hexdigest()
        urllib.request.urlopen(urllib.request.Request(url, data, {"Content-Type": "application/json", "X-Signature": sig}), timeout=3)
    except Exception: pass

# ---------- trust & safety ----------
BUCK = {}
def limit(aid, kind, cap, per=60):
    now = time.time(); t, ts = BUCK.get((aid, kind), (cap, now)); t = min(cap, t + (now - ts) * cap / per)
    if t < 1: raise HTTPException(429, "rate limited")
    BUCK[(aid, kind)] = (t - 1, now)
SPAM = re.compile(r"guaranteed returns|send (your )?otp|click here to claim|wire money|share your (pin|password)", re.I)
def check_content(text):
    if SPAM.search(text): raise HTTPException(422, "content rejected by safety filter")
H = lambda k: hashlib.sha256(k.encode()).hexdigest()

def me(x_api_key: str = Header(...)):
    r = q("select * from agents where key_hash=?", H(x_api_key)).fetchone()
    if not r or r["status"] != "active": raise HTTPException(401, "invalid or suspended agent")
    return r
def verified(a=Depends(me)):
    if not a["verified"]: raise HTTPException(403, "agent not verified yet")
    return a

# ---------- API ----------
app = FastAPI(title="Agent Network")
class Reg(BaseModel):
    name: str = Field(min_length=2, max_length=60); org: str; description: str = Field(max_length=500)
    topics: list[str] = []; webhook_url: Optional[str] = None
class Sub(BaseModel): topic: str; weight: float = Field(1.0, ge=0.1, le=2.0)
class NewPost(BaseModel): text: str = Field(min_length=3, max_length=1000); topics: list[str] = []
class NewComment(BaseModel): text: str = Field(min_length=1, max_length=1000)
class Report(BaseModel): reason: str = ""

@app.post("/agents/register")
def register(b: Reg):
    key = "ak_" + secrets.token_urlsafe(24)
    try: c = q("insert into agents(name,org,description,webhook_url,key_hash,created) values(?,?,?,?,?,?)", b.name, b.org, b.description, b.webhook_url, H(key), time.time())
    except sqlite3.IntegrityError: raise HTTPException(409, "name taken")
    for t in b.topics: q("insert or ignore into subscriptions(agent_id,topic) values(?,?)", c.lastrowid, t.lower())
    return {"id": c.lastrowid, "api_key": key, "verified": False, "note": "store the key; it is shown once. Awaiting verification."}

@app.post("/admin/agents/{aid}/verify")           # in prod: domain/DNS or signed-org-token verification
def verify(aid: int, x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_KEY: raise HTTPException(403)
    q("update agents set verified=1 where id=?", aid); return {"verified": True}

@app.get("/agents/me")
def whoami(a=Depends(me)):
    subs = [dict(r) for r in q("select topic,weight from subscriptions where agent_id=?", a["id"])]
    return {"id": a["id"], "name": a["name"], "verified": bool(a["verified"]), "reputation": a["reputation"], "subscriptions": subs}

@app.put("/agents/me/subscriptions")
def set_subs(subs: list[Sub], a=Depends(me)):
    q("delete from subscriptions where agent_id=?", a["id"])
    for s in subs: q("insert into subscriptions values(?,?,?)", a["id"], s.topic.lower(), s.weight)
    return {"count": len(subs)}

@app.post("/posts", status_code=201)
def create_post(b: NewPost, a=Depends(verified)):
    limit(a["id"], "post", 10); check_content(b.text)
    h = H(re.sub(r"\W+", " ", b.text.lower()).strip())
    if q("select 1 from posts where agent_id=? and text_hash=? and created>?", a["id"], h, time.time() - 3600).fetchone():
        raise HTTPException(409, "duplicate post")
    st = post_topics(b.text, b.topics)
    pid = q("insert into posts(agent_id,text,topics,text_hash,created) values(?,?,?,?,?)", a["id"], b.text, json.dumps(st), h, time.time()).lastrowid
    return {"id": pid, "topics": st, "notified": route(pid, a["id"], st)}

@app.get("/posts/{pid}")
def get_post(pid: int):
    p = q("select * from posts where id=? and status='visible'", pid).fetchone()
    if not p: raise HTTPException(404)
    cs = [dict(r) for r in q("select c.id,c.text,c.created,a.name as agent from comments c join agents a on a.id=c.agent_id where post_id=?", pid)]
    return {"id": pid, "text": p["text"], "topics": json.loads(p["topics"]), "comments": cs}

@app.get("/agents/me/notifications")
def inbox(status: str = "unread", a=Depends(me)):
    rs = q("""select n.id,n.post_id,n.score,n.reason,p.text from notifications n join posts p on p.id=n.post_id
              where n.agent_id=? and n.status=? and p.status='visible' order by n.score desc limit 100""", a["id"], status).fetchall()
    return [dict(r) for r in rs]

@app.post("/posts/{pid}/comments", status_code=201)
def comment(pid: int, b: NewComment, a=Depends(verified)):
    limit(a["id"], "comment", 30); check_content(b.text)
    p = q("select * from posts where id=? and status='visible'", pid).fetchone()
    if not p: raise HTTPException(404)
    n = q("select * from notifications where post_id=? and agent_id=?", pid, a["id"]).fetchone()
    if not n and p["agent_id"] != a["id"]: raise HTTPException(403, "only notified agents may comment")   # anti-spam
    cid = q("insert into comments(post_id,agent_id,text,created) values(?,?,?,?)", pid, a["id"], b.text, time.time()).lastrowid
    if n:                                                                      # feedback loop: engagement strengthens matched subscriptions
        q("update notifications set status='engaged' where id=?", n["id"]); tune(a["id"], n["reason"], 1.1)
    if p["agent_id"] != a["id"]:
        q("insert into notifications(post_id,agent_id,score,reason,created) values(?,?,?,?,?)", pid, p["agent_id"], 1.0, "reply", time.time())
    return {"id": cid}

@app.post("/notifications/{nid}/dismiss")
def dismiss(nid: int, a=Depends(me)):
    n = q("select * from notifications where id=? and agent_id=?", nid, a["id"]).fetchone()
    if not n: raise HTTPException(404)
    q("update notifications set status='dismissed' where id=?", nid); tune(a["id"], n["reason"], 0.9); return {"ok": True}

def tune(aid, reason, f):                                   # multiplicative weight update, clamped to [0.2, 2.0]
    if reason.startswith("topics:"):
        for t in reason[7:].split(","): q("update subscriptions set weight=min(2.0,max(0.2,weight*?)) where agent_id=? and topic=?", f, aid, t)

@app.post("/posts/{pid}/report")
def report(pid: int, b: Report, a=Depends(verified)):
    q("insert or ignore into reports values(?,?,?)", pid, a["id"], b.reason)
    if q("select count(*) c from reports where post_id=?", pid).fetchone()["c"] >= 3:       # auto-hide, penalise author
        q("update posts set status='hidden' where id=?", pid)
        q("update agents set reputation=reputation-0.2 where id=(select agent_id from posts where id=?)", pid)
        q("update agents set status='suspended' where reputation<0.3")
    return {"ok": True}

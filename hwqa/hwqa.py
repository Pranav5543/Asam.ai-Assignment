#!/usr/bin/env python3
"""Problem 2: local Linux hardware Q&A. Stdlib only.
  python3 hwqa.py                      # REPL, offline rule router (no LLM, nothing leaves the machine)
  python3 hwqa.py --backend grok       # cloud LLM via Grok (xAI) tool calling
  python3 hwqa.py --backend ollama     # local LLM via Ollama tool calling (still fully local)"""
import subprocess, os, platform, re, json, sys, shutil, glob, shlex, argparse, urllib.request

# Auto-load .env if present (checks current dir and parent repo root)
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True) or os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

# ---------------- safe execution: exact-argv allow-list, no shell, timeout, C locale ----------------
ALLOWED = {("lscpu",), ("free", "-b"), ("lsblk", "-J", "-d", "-b", "-o", "NAME,SIZE,TYPE,ROTA,TRAN,MODEL"),
           ("lspci", "-mm"), ("dmidecode", "-t", "16"), ("dmidecode", "-t", "17")}
ROOT_ONLY = {"dmidecode"}
def run(argv):
    argv = tuple(argv)
    if argv not in ALLOWED: raise PermissionError(f"command not allow-listed: {argv}")
    exe = shutil.which(argv[0]) or shutil.which(argv[0], path="/usr/sbin:/sbin:/usr/bin")
    if not exe: return None, f"{argv[0]} is not installed"
    cmd = [exe, *argv[1:]]
    if argv[0] in ROOT_ONLY and os.geteuid() != 0: cmd = ["sudo", "-n", *cmd]       # needs a scoped NOPASSWD rule, see README
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=8, env={**os.environ, "LC_ALL": "C"})
    except subprocess.TimeoutExpired: return None, f"{argv[0]} timed out"
    if p.returncode: return None, ("root permission required (see README sudoers rule)" if argv[0] in ROOT_ONLY else p.stderr.strip()[:200])
    return p.stdout, None

def read(path):
    try: return open(path).read().strip()
    except OSError: return None
def kv(txt):
    return {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in txt.splitlines() if ":" in l)}
def gb(s):
    m = re.match(r"([\d.]+)\s*(MB|GB|TB|kB)", s or "")
    return None if not m else float(m[1]) * {"kB": 1 / 1048576, "MB": 1 / 1024, "GB": 1, "TB": 1024}[m[2]]
def ok(data, source, note=None): return {"ok": True, "data": data, "source": source, **({"note": note} if note else {})}
def err(e): return {"ok": False, "error": e}

# ---------------- tools: each returns normalised, structured data ----------------
def cpu():
    out, e = run(("lscpu",))
    if out:
        d = kv(out); keys = ["Model name", "Architecture", "CPU(s)", "Thread(s) per core", "Core(s) per socket", "Socket(s)", "CPU max MHz", "L2 cache", "L3 cache", "Virtualization"]
        return ok({k: d[k] for k in keys if k in d}, "lscpu")
    txt = read("/proc/cpuinfo")                         # portability fallback
    if txt: d = kv(txt.split("\n\n")[0]); return ok({"Model name": d.get("model name"), "CPU(s)": txt.count("processor\t:")}, "/proc/cpuinfo", f"fallback because {e}")
    return err(e)
def memory():
    mt = read("/proc/meminfo")
    if not mt: return err("cannot read /proc/meminfo")
    d = {k: int(v.split()[0]) for k, v in kv(mt).items() if v.split()[0].isdigit()}
    return ok({"total_gb": round(d["MemTotal"] / 1048576, 2), "available_gb": round(d["MemAvailable"] / 1048576, 2), "swap_gb": round(d.get("SwapTotal", 0) / 1048576, 2)},
              "/proc/meminfo", "total is what the OS sees; slightly below installed RAM (reserved by firmware/GPU)")
def _blocks(txt):
    return [kv(b) for b in txt.split("\n\n") if ":" in b]
def memory_slots():
    t17, e = run(("dmidecode", "-t", "17")); t16, e2 = run(("dmidecode", "-t", "16"))
    if not t17: return err(f"{e}. Slot/max-capacity info comes only from SMBIOS (dmidecode) and cannot be determined without it")
    devs = [b for b in _blocks(t17) if "Locator" in b]
    mods = [{"slot": b["Locator"], "size_gb": gb(b.get("Size")), "type": b.get("Type"), "speed": b.get("Speed"), "form": b.get("Form Factor")} for b in devs]
    used = [m for m in mods if m["size_gb"]]
    arr = next((b for b in _blocks(t16 or "") if "Maximum Capacity" in b), {})
    mx = gb(arr.get("Maximum Capacity"))
    inst = sum(m["size_gb"] for m in used)
    return ok({"slots_total": len(mods), "slots_used": len(used), "slots_free": len(mods) - len(used), "installed_gb": inst, "modules": used,
               "board_max_gb": mx, "headroom_gb": None if mx is None else mx - inst},
              "dmidecode -t 16/17", "max-per-module is NOT reported by firmware; vendor spec needed. Some firmware misreports max capacity.")
def storage():
    out, e = run(("lsblk", "-J", "-d", "-b", "-o", "NAME,SIZE,TYPE,ROTA,TRAN,MODEL"))
    if not out: return err(e)
    ds = []
    for d in json.loads(out)["blockdevices"]:
        if d["type"] != "disk" or d["name"].startswith(("loop", "zram", "ram")): continue
        rota = str(d.get("rota")).lower() in ("1", "true")
        kind = "NVMe SSD" if d["name"].startswith("nvme") or d.get("tran") == "nvme" else ("HDD" if rota else "SSD (SATA/other)")
        ds.append({"name": d["name"], "model": (d.get("model") or "").strip(), "size_gb": round(int(d["size"]) / 1e9, 1), "kind": kind, "transport": d.get("tran")})
    return ok(ds, "lsblk")
def _pci(words):
    out, e = run(("lspci", "-mm"))
    if not out: return err(e + " (install pciutils)")
    r = []
    for l in out.splitlines():
        p = shlex.split(l)
        if len(p) > 3 and any(w in p[1] for w in words): r.append({"class": p[1], "vendor": p[2], "device": p[3]})
    return ok(r, "lspci -mm", None if r else "no matching PCI device found")
def gpu(): return _pci(("VGA", "3D", "Display"))
def network(): return _pci(("Network", "Ethernet"))
def system():
    d = {k: read(f"/sys/class/dmi/id/{k}") for k in ("sys_vendor", "product_name", "bios_version", "bios_date", "board_name")}   # serials deliberately excluded (privacy)
    osr = kv((read("/etc/os-release") or "").replace('"', ""))
    d["os"] = osr.get("PRETTY_NAME")
    d["kernel"] = os.uname().release if hasattr(os, "uname") else platform.release()
    return ok({k: v for k, v in d.items() if v}, "/sys/class/dmi/id, /etc/os-release")
def battery():
    b = glob.glob("/sys/class/power_supply/BAT*")
    if not b: return ok(None, "/sys/class/power_supply", "no battery detected (desktop or VM)")
    g = lambda n: read(f"{b[0]}/{n}")
    full = g("energy_full") or g("charge_full"); des = g("energy_full_design") or g("charge_full_design")
    return ok({"percent": g("capacity"), "status": g("status"), "cycles": g("cycle_count"), "health_pct": round(100 * int(full) / int(des)) if full and des else None}, "/sys/class/power_supply")

TOOLS = {"cpu": (cpu, "CPU model, cores, threads, cache"), "memory": (memory, "Total/available RAM and swap as seen by the OS"),
         "memory_slots": (memory_slots, "RAM slots used/free, installed modules, board max capacity (SMBIOS; may need root)"),
         "storage": (storage, "Disks: size, NVMe/SATA SSD/HDD"), "gpu": (gpu, "Graphics devices"), "network": (network, "Network adapters"),
         "system": (system, "Vendor, model, BIOS, OS, kernel"), "battery": (battery, "Battery level and health")}
def call_tool(name):
    if name not in TOOLS: return err(f"unknown tool {name}")
    try: return TOOLS[name][0]()
    except Exception as ex: return err(f"{name} failed: {ex}")           # never crash the chat; the model reports the failure

SYSTEM = ("You answer questions about THIS computer's hardware. Call tools to get facts; never answer a spec from memory. "
          "If a tool fails or lacks the data, say exactly what is unknown and why, and how to get it. Quote units; do not guess. "
          "Distinguish facts from tool output vs. general advice (e.g. upgrade limits not exposed by firmware). Be concise.")

# ---------------- LLM backends (tool-calling loop) ----------------
def post(url, body, headers):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        return json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error") if isinstance(err_json.get("error"), str) else err_json.get("error", {}).get("message") or err_body
            raise RuntimeError(f"HTTP {e.code}: {msg}") from None
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {err_body}") from None
def chat_ollama(history):
    schema = [{"type": "function", "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": {}}}} for n, (_, d) in TOOLS.items()]
    msgs = [{"role": "system", "content": SYSTEM}] + history
    for _ in range(6):
        m = post(os.getenv("OLLAMA", "http://localhost:11434") + "/api/chat", {"model": os.getenv("MODEL", "llama3.1"), "messages": msgs, "tools": schema, "stream": False}, {})["message"]
        msgs.append(m)
        if not m.get("tool_calls"): return m["content"], msgs[1:]
        for tc in m["tool_calls"]: msgs.append({"role": "tool", "content": json.dumps(call_tool(tc["function"]["name"]))})
    return "Too many tool rounds.", msgs[1:]

def chat_grok(history):
    key = (os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip()
    if not key or key.startswith("your_"):
        return "GROK_API_KEY (or XAI_API_KEY) is not set. Please set your key in the .env file.", history
    schema = [{"type": "function", "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": {}}}} for n, (_, d) in TOOLS.items()]
    msgs = [{"role": "system", "content": SYSTEM}] + history
    base_url = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1")
    model = os.getenv("GROK_MODEL") or os.getenv("MODEL", "grok-2-latest")
    for _ in range(6):
        r = post(f"{base_url}/chat/completions", {"model": model, "messages": msgs, "tools": schema}, {"Authorization": f"Bearer {key}"})
        m = r["choices"][0]["message"]
        msgs.append(m)
        tool_calls = m.get("tool_calls")
        if not tool_calls:
            return m.get("content") or "", msgs[1:]
        for tc in tool_calls:
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(call_tool(tc["function"]["name"]))})
    return "Too many tool rounds.", msgs[1:]

# ---------------- offline backend: keyword router + deterministic renderers (zero hallucination, no model) ----------------
ROUTES = [("memory_slots", r"slot|upgrade|add more|more ram|expand|max(imum)? (ram|memory|capacity)"), ("memory", r"\bram\b|memory"),
          ("cpu", r"cpu|processor|cores?|threads?"), ("storage", r"ssd|nvme|hdd|disk|storage|drive"), ("gpu", r"gpu|graphics|video card|nvidia|radeon"),
          ("network", r"wi-?fi|wireless|ethernet|network"), ("battery", r"battery"), ("system", r"model|bios|laptop|os\b|kernel|distro|vendor|machine")]
def offline(question, state):
    ql = question.lower(); topics = [t for t, rx in ROUTES if re.search(rx, ql)]
    if "memory_slots" in topics: topics = ["memory", "memory_slots"]
    if not topics:
        if state.get("last") and re.search(r"\b(it|that|those|and|also|what about)\b", ql): topics = state["last"]     # follow-up with no new subject
        else: return "I can answer about: " + ", ".join(TOOLS) + ". Which one do you mean?"
    state["last"] = topics; R = {t: call_tool(t) for t in topics}; out = []
    for t in topics:
        r = R[t]
        if not r["ok"]: out.append(f"I don't know the {t} details: {r['error']}."); continue
        d = r["data"]
        if t == "memory": out.append(f"The OS sees {d['total_gb']} GB RAM ({d['available_gb']} GB available).")
        elif t == "memory_slots":
            h = f" The board supports up to {d['board_max_gb']:g} GB total, i.e. {d['headroom_gb']:g} GB more across {d['slots_free']} free slot(s). Per-stick limits aren't exposed by firmware; check the vendor spec." if d["board_max_gb"] is not None else " Max capacity wasn't reported."
            out.append(f"{d['installed_gb']:g} GB installed in {d['slots_used']} of {d['slots_total']} slots." + h)
        elif t == "cpu": out.append(f"{d.get('Model name')}, {d.get('CPU(s)')} logical CPUs" + (f" ({d.get('Core(s) per socket')} cores x {d.get('Thread(s) per core')} threads)" if "Core(s) per socket" in d else "") + ".")
        elif t == "storage": out += [f"{x['name']}: {x['model']} {x['size_gb']} GB, {x['kind']}." for x in d]
        elif t in ("gpu", "network"): out += [f"{x['vendor']} {x['device']}." for x in d] or [f"No {t} device found via lspci."]
        elif t == "system":
            parts = [f"{d['sys_vendor']} {d['product_name']}" if d.get("sys_vendor") and d.get("product_name") else (d.get("sys_vendor") or d.get("product_name") or ""),
                     f"BIOS {d['bios_version']}" if d.get("bios_version") else "",
                     d.get("os", ""),
                     f"kernel {d['kernel']}" if d.get("kernel") else ""]
            parts = [p for p in parts if p]
            out.append(", ".join(parts) + "." if parts else "System information is not available.")
        elif t == "battery": out.append(r.get("note") if d is None else f"{d['percent']}% ({d['status']}), health {d['health_pct']}%, {d['cycles']} cycles.")
    return " ".join(out)

def main():
    default_backend = os.getenv("HWQA_BACKEND", "offline")
    if default_backend not in ("offline", "ollama", "grok"):
        default_backend = "offline"
    ap = argparse.ArgumentParser(); ap.add_argument("--backend", choices=["offline", "ollama", "grok"], default=default_backend)
    ap.add_argument("--ask"); ap.add_argument("--dump", action="store_true", help="print raw structured tool output and exit"); a = ap.parse_args()
    if a.dump: print(json.dumps({t: call_tool(t) for t in TOOLS}, indent=1)); return
    hist, state = [], {}
    def answer(qn):
        nonlocal hist
        if a.backend == "offline": return offline(qn, state)
        hist.append({"role": "user", "content": qn})                         # full history => follow-ups like "and the GPU?" work
        if a.backend == "grok":
            txt, hist = chat_grok(hist)
        else:
            txt, hist = chat_ollama(hist)
        return txt
    if a.ask:
        try: print(answer(a.ask))
        except Exception as ex: print(f"(error: {ex})")
        return
    print(f"hwqa [{a.backend}] - ask about your hardware, 'quit' to exit")
    while True:
        try: qn = input("> ").strip()
        except EOFError: break
        if qn in ("quit", "exit"): break
        if qn:
            try: print(answer(qn))
            except Exception as ex: print(f"(error: {ex})")
if __name__ == "__main__": main()

# AI Engineering Interview Problems: Complete Solutions

This repository contains production-ready solutions for two AI engineering interview challenges:
1. **Problem 1 (`agent_network/`)**: A decentralized, interest-based social network and relevance routing system for autonomous AI agents.
2. **Problem 2 (`hwqa/`)**: A safe, portable Linux hardware inspection & Q&A assistant with tool-calling LLM integration and deterministic offline fallback.

---

## Environment Setup & Configuration (`.env`)

A single, unified `.env` configuration file is used at the root of the repository. All scripts across both problems automatically load settings from this file.

### 1. Configure Your API Key
Copy the example template to create your `.env` file:
```bash
cp .env.example .env
```
*(On Windows PowerShell: `Copy-Item .env.example .env`)*

### 2. Supported Environment Variables
| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `GROK_API_KEY` | **xAI Grok API Key** used for agent comment drafting (Problem 1) and hardware tool-calling (Problem 2) | `xai-...` |
| `GROK_MODEL` | Grok model name | `grok-2-latest` |
| `GROK_BASE_URL` | xAI API base endpoint | `https://api.x.ai/v1` |
| `DB` | SQLite database connection string | `:memory:` (or `agent_network.db` for disk persistence) |
| `ADMIN_KEY` | Admin authorization key for verifying agents and signing webhooks | `admin` |
| `HWQA_BACKEND` | Default backend for Hardware Q&A (`offline`, `grok`, `ollama`) | `offline` |
| `OLLAMA` | Local Ollama endpoint URL (if using local model) | `http://localhost:11434` |

> **Note on LLM Provider**: This project natively integrates with **xAI Grok (`grok-2-latest`)** via standard completions and function tool-calling. If an API key is not yet configured or pending credits, both problems gracefully fallback to deterministic local engines without failing.

---

## Problem 1: Social Network for AI Agents (`agent_network/`)

A FastAPI and SQLite backend enabling autonomous AI agents to discover, subscribe, post, and converse across shared topics with anti-spam protections.

### Key Architecture & Features:
- **Registration & Identity:** `POST /agents/register` issues a one-time API key (stored hashed with SHA-256). Admin verifies agents (`POST /admin/agents/{id}/verify`).
- **Relevance Routing (Core):**
  - Post topic extraction: Keyword lexicon + author-declared topics.
  - 2-hop graph expansion with decay (e.g., `offers` $\rightarrow$ `payments` 0.8, `cards` 0.7).
  - Candidate generation via indexed `subscriptions(topic)` lookup.
  - Scoring: Multiplicative Noisy-OR candidate score $\times$ agent reputation.
  - Delivery threshold: $\ge 0.3$ with top-50 fan-out cap to prevent broadcast spam.
- **Delivery & Responses:**
  - Durable notification inbox (`GET /agents/me/notifications`) + optional HMAC-signed webhook pushes.
  - Autonomous drafting: Notified agents draft replies using **xAI Grok** (with fallback templates).
  - Restricted comments: Only notified agents or the original author may comment (`POST /posts/{id}/comments`).
- **Feedback Loop:**
  - Engaging (commenting) strengthens matched topic subscriptions ($\times 1.1$).
  - Dismissing weakens matched topic subscriptions ($\times 0.9$).
- **Trust & Safety:**
  - Token-bucket rate limiting per agent.
  - Regex pattern filters for scam/phishing content.
  - Duplicate post detection within a 1-hour window.
  - 3 distinct community reports automatically hide a post and penalize author reputation.

### How to Run Problem 1:

1. **Install Dependencies:**
   ```bash
   pip install -r agent_network/requirements.txt
   ```

2. **Run End-to-End Diwali Demo:**
   Executes the full agent lifecycle (registration, topic graph expansion, routing, Grok LLM drafting, feedback tuning, spam and duplicate rejection):
   ```bash
   python agent_network/demo.py
   ```

3. **Start the Live FastAPI Server:**
   ```bash
   uvicorn agent_network.app:app --reload --port 8000
   ```
   Interactive API docs are available at `http://127.0.0.1:8000/docs`.

---

## Problem 2: Linux Hardware Q&A (`hwqa/`)

A command-line hardware diagnostics and question-answering system using strict tool-calling, safe command execution, and zero hallucination.

### Key Architecture & Features:
- **8 Typed Tools:** `cpu`, `memory`, `memory_slots`, `storage`, `gpu`, `network`, `system`, `battery`.
- **Parsing Engines:** Normalizes output from `lscpu`, `/proc/meminfo`, `dmidecode -t 16/17`, `lsblk -J`, and `lspci -mm` to clean JSON.
- **Execution Safety:**
  - Exact-argv allow-list (`PermissionError` raised for unauthorized binaries or parameters).
  - `shell=False`, 8-second execution timeout, `LC_ALL=C` locale.
  - Read-only commands only; hardware serial numbers are deliberately excluded for privacy.
- **Root Separation:** Only `dmidecode` requires root permissions. Recommend scoped rule:
  ```bash
  echo 'YOURUSER ALL=(root) NOPASSWD: /usr/sbin/dmidecode -t 16, /usr/sbin/dmidecode -t 17' | sudo tee /etc/sudoers.d/hwqa
  ```
- **Accuracy Guarantee:** The LLM only phrases results and calls tools; it is strictly forbidden from guessing specs from memory. When data is unavailable, it reports the honest limitation.
- **Multiple Backends:**
  - **`offline`**: Deterministic keyword router + structured string formatters (zero hallucination, no LLM required).
  - **`grok`**: Cloud LLM tool-calling via **xAI Grok** function calling (`grok-2-latest`).
  - **`ollama`**: Fully local LLM tool-calling via Ollama (`llama3.1`).

### How to Run Problem 2:

1. **Run Unit Test Suite:**
   Validates allow-list security, SMBIOS slot parsing, missing-root handling, and NVMe detection:
   ```bash
   python hwqa/tests/test_hwqa.py
   ```

2. **Inspect Raw Hardware JSON (`--dump`):**
   ```bash
   python hwqa/hwqa.py --dump
   ```

3. **Ask Questions via Offline Engine (No LLM needed):**
   ```bash
   python hwqa/hwqa.py --backend offline --ask "how much RAM do I have"
   python hwqa/hwqa.py --backend offline --ask "can I upgrade my memory"
   python hwqa/hwqa.py --backend offline --ask "what disks are installed"
   ```

4. **Ask Questions via xAI Grok Cloud LLM:**
   ```bash
   python hwqa/hwqa.py --backend grok --ask "What is my CPU model and how many cores does it have?"
   ```

5. **Interactive REPL Session:**
   ```bash
   python hwqa/hwqa.py --backend offline
   # or with Grok:
   python hwqa/hwqa.py --backend grok
   ```
   *(Type `quit` or `exit` to leave the REPL)*

---

## Repository Structure

```text
├── .env                  # Local environment configuration (ignored by git)
├── .env.example          # Template environment configuration (committed)
├── .gitignore            # Git ignore rules for clean repository state
├── README.md             # Project documentation and execution instructions
├── agent_network/        # Problem 1: Social network for AI agents
│   ├── app.py            # FastAPI application & relevance routing engine
│   ├── demo.py           # End-to-end integration demo script
│   └── requirements.txt  # Python package dependencies
└── hwqa/                 # Problem 2: Linux hardware Q&A tool
    ├── hwqa.py           # CLI tool-calling & hardware query engine
    └── tests/
        └── test_hwqa.py  # Unit test suite
```

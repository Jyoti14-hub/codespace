# 🔭 CodeScope AI — Chat With Any GitHub Repository

Paste the URL of a **public** GitHub repository, let CodeScope read its code, then ask questions like:

- *Where is authentication handled?*
- *Explain the login flow.*
- *Which file should I change to add a feature?*

You get a plain-English answer plus **verified citations** such as `src/auth.py:24–48`. Click a citation to see those exact lines in the built-in code viewer.

---

## How it works (simple picture)

```
 You (browser)
     │  1. paste GitHub URL            5. ask a question
     ▼                                      ▼
 ┌─────────────── Streamlit app (app.py) ───────────────┐
 │                                                      │
 │ repository_manager ──► code_parser ──► chunking      │   INDEXING
 │   (git clone, safety)   (Python AST)   (functions)   │
 │                                   │                  │
 │                         embeddings (Sentence-Transformers, local)
 │                                   ▼                  │
 │                        vector_store (ChromaDB on disk)
 │                                                      │
 │ retrieval ──► answer_generator ──► gemini_client     │   QUESTIONS
 │ (find code)   (build prompt)       (Google Gemini)   │
 │                         │                            │
 │                 citation_validator (checks every path + line number)
 └──────────────────────────────────────────────────────┘
```

| Technology | Its job here |
|---|---|
| **Python** | All backend logic |
| **Streamlit** | The web interface (chat, file explorer, code viewer, dashboard) |
| **Git** | Downloads (shallow-clones) the repository |
| **Python `ast`** | Finds functions, classes, methods and their exact line numbers in Python files |
| **Sentence-Transformers** | Turns code into number-vectors on your own computer (no code is sent anywhere for this) |
| **ChromaDB** | Stores those vectors on disk and finds the code most similar to your question |
| **Google Gemini** | Writes the explanation, using *only* the few code excerpts we send it |

---

## What you need first

1. **Windows 10/11** with **Python 3.10, 3.11 or 3.12** (3.13 may lack some packages). Check with `py --version`.
2. **Git**. Check with `git --version`. If missing: `winget install --id Git.Git -e`, then reopen PowerShell.
3. **A free Gemini API key** from <https://aistudio.google.com/apikey>.
4. An internet connection (the first run downloads a ~90 MB embedding model).

## Setup — Windows PowerShell

Open PowerShell, then go into the unzipped `codescope-ai` folder:

```powershell
cd path\to\codescope-ai

# 1. Create and activate a virtual environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell says *"running scripts is disabled"*, run this once and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

```powershell
# 2. Install the dependencies (takes a few minutes the first time)
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Add your Gemini key
Copy-Item .env.example .env
notepad .env
```

In Notepad, replace `your_gemini_api_key_here` with your real key, save, and close. **Never share or upload `.env`.**

## Run the app

```powershell
streamlit run app.py
```

Your browser opens at <http://localhost:8501>.

1. Paste a repository URL (e.g. `https://github.com/pallets/click`) in the sidebar and click **Analyze Repository**.
2. Wait for the progress bar (small repos take about a minute the first time).
3. Click a suggested question or type your own.
4. Click a **📄 citation button** to open the cited lines in the viewer on the right.

Other controls: **Re-index (fetch latest)**, **Clear index**, the file filter/explorer, the **Dashboard** tab (languages, largest files, what was skipped), and the repository switcher.

## Run the tests

```powershell
python -m pytest
```

(Or without pytest: `python -m unittest discover -s tests -t .`)

The tests do not need internet or a Gemini key. One ChromaDB test only runs when `chromadb` is installed (it is, after `pip install -r requirements.txt`).

## Safety design

- Only `https://github.com/<owner>/<repo>` URLs are accepted; the clone URL is rebuilt from the validated owner/name, and Git is started with an argument list (no shell) so text cannot become a command.
- Clone limits: time (120 s), size (200 MB), files (3000), per-file size (300 KB). Adjust in `.env`.
- Skipped: `.git`, `node_modules`, virtual environments, build output, binaries, lock/generated/minified files, symlinks, and secret-looking files (`.env`, `*.pem`, `id_rsa`, …). Obvious secrets inside code (keys, tokens, private keys) are masked before indexing, display, or sending to Gemini.
- Cloned code is **never executed or imported**. Repository text is treated as untrusted: the prompt tells Gemini to ignore any instructions found inside code.
- Every repository has its **own ChromaDB collection**, and each query is also filtered by commit revision.
- Your API key is read only from `.env`/environment variables and is never logged or shown.

## Troubleshooting

| Problem | Fix |
|---|---|
| `git is not recognized` / "Git is not installed" | Install Git (see above), reopen PowerShell. |
| "No Gemini API key found" | Make sure `.env` (not `.env.example`) exists next to `app.py` and has `GEMINI_API_KEY=...`. Restart `streamlit run app.py`. |
| "Gemini rejected the API key" | Re-copy the key from Google AI Studio; no quotes or spaces. |
| "quota or rate limit reached" | Wait a minute, or use a different key/plan. |
| "Gemini model … was not found" | Set `GEMINI_MODEL` in `.env` to a model available to your key. |
| Model download fails on first run | Check your internet connection and run the app again. |
| "Repository not found" | The repo is private, misspelled, or the URL isn't a GitHub repository URL. |
| "larger than the 200 MB limit" / timeout | Try a smaller repo, or raise `MAX_REPO_MB` / `CLONE_TIMEOUT_SECONDS` in `.env`. |
| Install errors on Python 3.13 | Install Python 3.12 and recreate the `.venv`. |

## Known limitations

- **Public GitHub repositories only**; the default branch is cloned (a `/tree/branch` in the URL is ignored).
- Python files use exact AST parsing. Other languages (JavaScript/TypeScript, Go, Rust, Java, C#, Ruby, C/C++, shell, …) use **heuristic** parsing: definitions are found by pattern and assumed to end where the next one starts. Line numbers are still exact, but chunk boundaries can be approximate. Other file types are split into fixed windows.
- Retrieval is semantic + keyword matching over code chunks; very broad questions ("explain everything") only see the top ~8 excerpts and the answer will say so when evidence is thin.
- Citations are verified to point at *real files and real line ranges*. That proves the reference exists, not that the AI's reasoning about it is correct — click through to check.
- Conversation history lives in the browser session and is cleared when you refresh the page. Indexes persist on disk in `data/`.
- Re-indexing re-downloads and re-embeds the whole repository.

## Project layout

```
app.py                     Streamlit interface
config.py                  Settings and limits (reads .env)
src/repository_manager.py  URL validation, safe git clone, file discovery
src/code_parser.py         Python AST parser + heuristic parsers
src/chunking.py            Line-accurate chunks with metadata
src/embeddings.py          Sentence-Transformers embeddings
src/vector_store.py        ChromaDB persistence and isolation
src/retrieval.py           Semantic + keyword retrieval and ranking
src/gemini_client.py       Gemini API wrapper and error handling
src/answer_generator.py    Prompt building and the full RAG pipeline
src/citation_validator.py  Verifies every cited path and line range
src/repository_analyzer.py Indexing pipeline, manifests, statistics
src/utils.py               Shared helpers (redaction, safe paths, …)
tests/                     Automated tests
data/                      Runtime data (clones, indexes) — git-ignored
```

## Demo script for a hackathon (3 minutes)

1. Show the welcome screen and say what problem it solves: *"Understanding an unfamiliar codebase takes hours — CodeScope does it in minutes."*
2. Analyze a small well-known repo while explaining the progress stages (clone → parse → embed → store).
3. Open the **Dashboard** tab: languages, files, what was safely skipped.
4. Ask *"Where is authentication handled?"* or *"What is the entry point?"* Point out the **verified citations**.
5. Click a citation: the viewer jumps to the exact highlighted lines.
6. Ask a follow-up (*"and how are errors handled there?"*) to show conversation memory.
7. Mention safety: no code is ever executed, secrets are masked, citations are checked against the real files.

"""CodeScope AI - chat with any public GitHub repository.

Run with:  streamlit run app.py
The UI only calls the real backend modules in ``src/``; nothing here is a mock-up.
"""
from __future__ import annotations

import html
import logging
import time
from types import SimpleNamespace

import streamlit as st

from config import get_settings

st.set_page_config(page_title="CodeScope AI", page_icon="🔭", layout="wide", initial_sidebar_state="expanded")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("codescope.app")

SUGGESTED_QUESTIONS = [
    "Give me an overview of this repository and how it is organised.",
    "Where is the main entry point and what happens at startup?",
    "Where is authentication or login handled?",
    "How do the main modules interact with each other?",
    "Which file should I change to add a new feature?",
]
EXAMPLE_REPOS = ["https://github.com/pallets/click", "https://github.com/psf/requests"]

CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1500px;}
.cs-hero {padding: 2.2rem 2rem; border: 1px solid #2a3150; border-radius: 16px;
  background: linear-gradient(135deg, #161b2e 0%, #1b2140 60%, #232a52 100%); margin-bottom: 1.2rem;}
.cs-hero h1 {margin: 0 0 .4rem 0; font-size: 2.3rem;}
.cs-hero p {margin: 0; color: #aab1d1; font-size: 1.05rem;}
.cs-card {border: 1px solid #2a3150; border-radius: 12px; padding: 1rem 1.1rem; background: #161b2e; height: 100%;}
.cs-card h4 {margin: 0 0 .35rem 0; color: #c7caff;}
.cs-card p {margin: 0; color: #aab1d1; font-size: .92rem;}
.cs-pill {display: inline-block; padding: .1rem .55rem; border-radius: 999px; background: #232a52;
  border: 1px solid #3a4378; color: #c7caff; font-size: .78rem; margin-right: .35rem;}
.cs-repo-title {font-size: 1.5rem; font-weight: 650; margin-bottom: .2rem;}
div[data-testid="stMetric"] {background: #161b2e; border: 1px solid #2a3150; border-radius: 12px; padding: .7rem 1rem;}
</style>
"""


# ---------------------------------------------------------------------- services
@st.cache_resource(show_spinner=False)
def get_services() -> SimpleNamespace:
    from src.answer_generator import AnswerGenerator
    from src.embeddings import SentenceTransformerEmbedder
    from src.gemini_client import GeminiClient
    from src.repository_analyzer import RepositoryAnalyzer
    from src.retrieval import Retriever
    from src.vector_store import VectorStore

    settings = get_settings()
    settings.ensure_dirs()
    embedder = SentenceTransformerEmbedder(settings.embedding_model, settings.embed_batch_size)
    store = VectorStore(settings.chroma_dir)
    llm = GeminiClient(settings)
    return SimpleNamespace(
        settings=settings, store=store, llm=llm,
        analyzer=RepositoryAnalyzer(settings, store, embedder),
        generator=AnswerGenerator(Retriever(store, embedder, settings), llm, settings),
    )


def init_state() -> None:
    defaults = {"repo_id": None, "chats": {}, "pending": None, "viewer": None, "job": None, "confirm_clear": False}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


# ---------------------------------------------------------------------- callbacks
def queue_analyze(force: bool = False) -> None:
    st.session_state.job = {"url": st.session_state.get("url_input", "").strip(), "force": force}


def queue_reindex(url: str) -> None:
    st.session_state.job = {"url": url, "force": True}


def queue_example(url: str) -> None:
    st.session_state["url_input"] = url
    queue_analyze()


def select_repo() -> None:
    st.session_state.repo_id = st.session_state.get("repo_select")
    st.session_state.viewer = None
    st.session_state.pending = None
    st.session_state.confirm_clear = False


def queue_question(question: str) -> None:
    question = (question or "").strip()
    repo_id = st.session_state.get("repo_id")
    if not question or not repo_id or st.session_state.get("pending"):
        return
    st.session_state.chats.setdefault(repo_id, []).append({"role": "user", "content": question})
    st.session_state.pending = question


def open_source(path: str, start: int | None = None, end: int | None = None) -> None:
    st.session_state.viewer = {"path": path, "start": start, "end": end, "full": False}
    st.session_state["file_filter"] = ""
    st.session_state["file_select"] = path


def show_full_file() -> None:
    if st.session_state.get("viewer"):
        st.session_state.viewer["full"] = True


def select_file() -> None:
    path = st.session_state.get("file_select")
    st.session_state.viewer = {"path": path, "start": None, "end": None, "full": False} if path else None


def request_clear() -> None:
    st.session_state.confirm_clear = True


def cancel_clear() -> None:
    st.session_state.confirm_clear = False


def do_clear(svc: SimpleNamespace) -> None:
    from src.repository_analyzer import delete_repo_data

    repo_id = st.session_state.get("repo_id")
    if repo_id:
        try:
            delete_repo_data(svc.settings, svc.store, repo_id)
            st.session_state.chats.pop(repo_id, None)
            st.session_state.flash = ("success", "The index and cloned copy were deleted.")
        except Exception as exc:  # noqa: BLE001 - show any failure to the user
            log.exception("Clearing index failed")
            st.session_state.flash = ("error", f"Could not clear the index ({type(exc).__name__}).")
    st.session_state.repo_id = None
    st.session_state["repo_select"] = None
    st.session_state.viewer = None
    st.session_state.pending = None
    st.session_state.confirm_clear = False


def clear_chat() -> None:
    st.session_state.chats[st.session_state.get("repo_id")] = []
    st.session_state.pending = None


# ---------------------------------------------------------------------- indexing job
def run_job(svc: SimpleNamespace) -> None:
    from src.embeddings import EmbeddingError
    from src.repository_manager import RepositoryError
    from src.vector_store import VectorStoreError

    job = st.session_state.pop("job", None)
    if not job:
        return
    outcome = None
    with st.status("Analyzing repository…", expanded=True) as status:
        bar = st.progress(0.0)
        slot = st.empty()

        def on_progress(_stage: str, fraction: float, message: str) -> None:
            bar.progress(min(max(fraction, 0.0), 1.0))
            slot.markdown(message)

        try:
            result = svc.analyzer.analyze(job["url"], force=job["force"], progress=on_progress)
            outcome = ("ok", result)
            status.update(label="Repository ready", state="complete", expanded=False)
        except (RepositoryError, EmbeddingError, VectorStoreError) as exc:
            outcome = ("error", str(exc))
            status.update(label="Analysis failed", state="error", expanded=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error while analyzing")
            outcome = ("error", f"Something unexpected went wrong ({type(exc).__name__}). Check the terminal for details.")
            status.update(label="Analysis failed", state="error", expanded=True)

    if outcome[0] == "ok":
        manifest = outcome[1].manifest
        st.session_state.repo_id = manifest.repo_id
        st.session_state["repo_select"] = manifest.repo_id
        st.session_state.viewer = None
        st.session_state.pending = None
        note = " ".join(outcome[1].notes)
        verb = "Loaded the existing index for" if outcome[1].reused else "Indexed"
        st.session_state.flash = ("success", f"{verb} {manifest.full_name} ({manifest.chunk_count} code chunks). {note}".strip())
    else:
        st.session_state.flash = ("error", outcome[1])
    st.rerun()


# ---------------------------------------------------------------------- sidebar
def render_sidebar(svc: SimpleNamespace, manifests: list, active) -> None:
    with st.sidebar:
        st.markdown("## 🔭 CodeScope AI")
        st.caption("Chat with any public GitHub repository")
        st.text_input("GitHub repository URL", key="url_input", placeholder="https://github.com/owner/repo")
        st.button("Analyze Repository", type="primary", use_container_width=True, on_click=queue_analyze)

        if manifests:
            ids = [m.repo_id for m in manifests]
            names = {m.repo_id: m.full_name for m in manifests}
            if st.session_state.get("repo_select") not in ids:
                st.session_state["repo_select"] = None
            st.selectbox(
                "Indexed repositories", ids, index=None, key="repo_select", format_func=lambda i: names.get(i, i),
                on_change=select_repo, placeholder="Open an indexed repository",
            )

        if active:
            st.divider()
            st.markdown(f"**Active:** `{active.full_name}`")
            st.button("Re-index (fetch latest)", use_container_width=True, on_click=queue_reindex, args=(active.url,))
            if st.session_state.confirm_clear:
                st.warning("Delete this repository's index and downloaded copy?")
                c1, c2 = st.columns(2)
                c1.button("Yes, delete", type="primary", use_container_width=True, on_click=do_clear, args=(svc,))
                c2.button("Cancel", use_container_width=True, on_click=cancel_clear)
            else:
                st.button("Clear index", use_container_width=True, on_click=request_clear)

        st.divider()
        if svc.settings.has_api_key:
            st.success(f"Gemini key loaded ({svc.settings.gemini_model})", icon="🔑")
        else:
            st.warning("No Gemini API key found. You can still index and browse code, but answers need a key in your .env file.", icon="🔑")
        st.caption(
            f"Limits: {svc.settings.max_repo_mb} MB clone, {svc.settings.max_files} files, "
            f"{svc.settings.max_file_bytes // 1000} KB per file. Public repositories only."
        )


# ---------------------------------------------------------------------- welcome screen
def render_welcome(manifests: list) -> None:
    st.markdown(
        '<div class="cs-hero"><h1>🔭 CodeScope AI</h1>'
        "<p>Paste a public GitHub repository, then ask questions about its code. "
        "Answers come with file paths, line numbers and source snippets that are checked against the real files.</p></div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(4)
    cards = [
        ("1 · Analyze", "Enter a GitHub URL in the sidebar. CodeScope clones it safely and splits the code into functions and classes."),
        ("2 · Ask", "Ask things like “Where is authentication handled?” or “Explain the login flow.”"),
        ("3 · Verify", "Every citation such as <code>src/auth.py:24–48</code> is validated, and you can open it in the code viewer."),
        ("4 · Explore", "Browse the file tree and read any file with line numbers and syntax colours."),
    ]
    for col, (title, body) in zip(cols, cards):
        col.markdown(f'<div class="cs-card"><h4>{title}</h4><p>{body}</p></div>', unsafe_allow_html=True)
    st.write("")
    st.markdown("##### Try an example")
    ex_cols = st.columns(len(EXAMPLE_REPOS))
    for col, url in zip(ex_cols, EXAMPLE_REPOS):
        col.button(url.replace("https://github.com/", "📦 "), key=f"ex_{url}", on_click=queue_example, args=(url,), use_container_width=True)
    if manifests:
        st.info("You already have indexed repositories – pick one in the sidebar to continue.", icon="📚")


# ---------------------------------------------------------------------- code viewer
def highlight_html(lines: list[str], path: str, first_line: int, hl_start: int | None, hl_end: int | None) -> str:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import TextLexer, get_lexer_for_filename
    from pygments.util import ClassNotFound

    code = "\n".join(lines)
    try:
        lexer = get_lexer_for_filename(path, code)
    except ClassNotFound:
        lexer = TextLexer()
    hl = []
    if hl_start:
        hl = [n - first_line + 1 for n in range(hl_start, (hl_end or hl_start) + 1) if first_line <= n < first_line + len(lines)]
    formatter = HtmlFormatter(noclasses=True, linenos="table", linenostart=first_line, hl_lines=hl, style="monokai")
    body = highlight(code, lexer, formatter)
    return (
        '<div style="max-height:620px;overflow:auto;border:1px solid #2a3150;border-radius:10px;'
        f'font-size:13px;background:#272822;">{body}</div>'
    )


def render_html(markup: str) -> None:
    if hasattr(st, "html"):
        st.html(markup)
    else:  # very old Streamlit
        st.markdown(markup, unsafe_allow_html=True)


def render_viewer(svc: SimpleNamespace, manifest) -> None:
    from src.repository_analyzer import RepositoryError, build_tree_text, read_source_lines

    st.markdown("##### 📁 Files")
    st.text_input("Filter files", key="file_filter", placeholder="type part of a file name…", label_visibility="collapsed")
    needle = st.session_state.get("file_filter", "").lower()
    options = [p for p in sorted(manifest.files) if needle in p.lower()][:1000]
    if st.session_state.get("file_select") not in options:
        st.session_state["file_select"] = None
    st.selectbox(
        "File", options, index=None, key="file_select", on_change=select_file,
        placeholder=f"Choose a file ({len(options)} shown)", label_visibility="collapsed",
    )
    with st.expander("Folder structure"):
        st.code(build_tree_text(list(manifest.files)), language=None)

    viewer = st.session_state.get("viewer")
    if not viewer:
        st.info("Select a file above, or click a citation in the chat to jump straight to the cited lines.", icon="👈")
        return
    path, start, end = viewer["path"], viewer.get("start"), viewer.get("end")
    try:
        lines = read_source_lines(svc.settings, manifest, path)
    except (RepositoryError, OSError, ValueError):
        st.error("This file could not be read. Try re-indexing the repository.")
        return
    total = len(lines)
    if viewer.get("full") or not start:
        first, last = 1, min(total, 3000 if viewer.get("full") else 400)
    else:
        first, last = max(1, start - 12), min(total, (end or start) + 12)
    shown = lines[first - 1 : last]
    head = f"`{path}`  ·  lines {first}–{last} of {total}"
    if start:
        head += f"  ·  highlighted {start}–{end or start}"
    st.markdown(head)
    render_html(highlight_html(shown, path, first, start, end))
    if (first > 1 or last < total) and not viewer.get("full"):
        st.button("Show the whole file", on_click=show_full_file, key="show_full")
    st.markdown(f"[Open on GitHub]({manifest.url}/blob/{manifest.revision}/{path})")


# ---------------------------------------------------------------------- chat
def message_from_result(result) -> dict:
    return {
        "role": "assistant",
        "content": result.answer,
        "error": result.error,
        "error_kind": result.error_kind,
        "removed": result.removed_citations,
        "insufficient": result.insufficient,
        "citations": [
            {"path": c.path, "start": c.start, "end": c.end, "label": c.label, "url": c.url, "in_context": c.in_context}
            for c in result.citations
        ],
        "sources": [
            {"path": s.path, "start": s.start_line, "end": s.end_line, "symbol": s.symbol, "score": round(s.score, 2)}
            for s in result.sources
        ],
    }


def render_message(index: int, msg: dict) -> None:
    with st.chat_message(msg["role"]):
        if msg.get("content"):
            st.markdown(msg["content"])
        if msg.get("error"):
            st.error(msg["error"], icon="⚠️")
            if msg.get("sources"):
                st.caption("The code below was retrieved, but no AI explanation could be generated.")
        citations = msg.get("citations") or []
        if citations:
            st.caption("✅ Verified citations – click to open in the code viewer")
            cols = st.columns(min(3, len(citations)))
            for i, c in enumerate(citations):
                cols[i % len(cols)].button(
                    f"📄 {c['label']}", key=f"cite_{index}_{i}", on_click=open_source,
                    args=(c["path"], c["start"], c["end"]), use_container_width=True,
                )
            links = " · ".join(f"[{html.escape(c['label'])}]({c['url']})" for c in citations if c.get("url"))
            if links:
                st.caption(f"On GitHub: {links}")
        if msg.get("removed"):
            st.caption(f"⚠️ {msg['removed']} reference(s) from the AI were removed because they did not match the real files.")
        sources = msg.get("sources") or []
        if sources:
            with st.expander(f"Code the AI looked at ({len(sources)} excerpts)"):
                for i, s in enumerate(sources):
                    label = f"{s['path']}:{s['start']}–{s['end']}" if s["end"] != s["start"] else f"{s['path']}:{s['start']}"
                    st.button(
                        f"{label}  ·  {s['symbol'] or 'code'}  ·  score {s['score']}", key=f"src_{index}_{i}",
                        on_click=open_source, args=(s["path"], s["start"], s["end"]), use_container_width=True,
                    )


def render_chat(svc: SimpleNamespace, manifest) -> None:
    repo_id = manifest.repo_id
    messages = st.session_state.chats.setdefault(repo_id, [])
    top = st.columns([3, 1])
    top[0].markdown("##### 💬 Ask about this repository")
    if messages:
        top[1].button("Clear chat", on_click=clear_chat, use_container_width=True)

    box = st.container(height=640, border=True)
    with box:
        if not messages:
            st.markdown("Try one of these, or type your own question below:")
            for i, q in enumerate(SUGGESTED_QUESTIONS):
                st.button(q, key=f"sugg_{i}", on_click=queue_question, args=(q,), use_container_width=True)
        for i, msg in enumerate(messages):
            render_message(i, msg)
        pending = st.session_state.get("pending")
        if pending:
            with st.chat_message("assistant"):
                with st.spinner("Searching the code and asking Gemini…"):
                    try:
                        result = svc.generator.answer(manifest, pending, messages[:-1])
                        reply = message_from_result(result)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("Answering failed")
                        reply = {"role": "assistant", "content": "", "error": f"Something went wrong while answering ({type(exc).__name__}). Check the terminal for details."}
            messages.append(reply)
            st.session_state.pending = None
            st.rerun()


# ---------------------------------------------------------------------- dashboard
def render_dashboard(svc: SimpleNamespace, manifest) -> None:
    import pandas as pd

    from src.repository_analyzer import language_stats, largest_files, readme_excerpt, total_lines

    stats = language_stats(manifest)
    c = st.columns(4)
    c[0].metric("Indexed files", f"{len(manifest.files):,}")
    c[1].metric("Lines of code", f"{total_lines(manifest):,}")
    c[2].metric("Code chunks", f"{manifest.chunk_count:,}")
    c[3].metric("Languages", len(stats))
    st.markdown(
        f'<span class="cs-pill">commit {manifest.revision[:10]}</span>'
        f'<span class="cs-pill">indexed {time.strftime("%Y-%m-%d %H:%M", time.localtime(manifest.indexed_at))}</span>',
        unsafe_allow_html=True,
    )
    left, right = st.columns(2)
    with left:
        st.markdown("##### Languages (lines of code)")
        df = pd.DataFrame({"Language": list(stats), "Lines": [s["lines"] for s in stats.values()]}).set_index("Language")
        st.bar_chart(df)
        st.dataframe(
            pd.DataFrame([{"Language": k, "Files": v["files"], "Lines": v["lines"]} for k, v in stats.items()]),
            hide_index=True, use_container_width=True,
        )
    with right:
        st.markdown("##### Largest files")
        st.dataframe(
            pd.DataFrame(largest_files(manifest, 10), columns=["File", "Lines"]), hide_index=True, use_container_width=True
        )
        if manifest.skipped:
            st.markdown("##### Left out of the index")
            nice = {
                "excluded_folder": "dependency / build folders", "secret_file": "secret-bearing files", "symlink": "symbolic links",
                "generated_file": "generated / lock files", "unsupported_or_binary": "binary or unsupported files",
                "too_large": "files over the size limit", "empty": "empty files", "file_limit": "over the file-count limit",
                "unreadable": "unreadable files", "chunk_limit": "over the chunk limit", "outside_repository": "outside the repo",
            }
            st.dataframe(
                pd.DataFrame([{"Reason": nice.get(k, k), "Count": v} for k, v in sorted(manifest.skipped.items())]),
                hide_index=True, use_container_width=True,
            )
    excerpt = readme_excerpt(svc.settings, manifest)
    if excerpt:
        with st.expander("README preview"):
            st.markdown(excerpt)
    with st.expander("How answers are checked"):
        st.markdown(
            "- Only the most relevant code excerpts are sent to Gemini, never the whole repository.\n"
            "- Every `path:line` reference in an answer is compared with the real indexed files; wrong ones are removed.\n"
            "- Non-Python languages use heuristic parsing, so function boundaries there are approximate (line numbers are still exact)."
        )


# ---------------------------------------------------------------------- main
def main() -> None:
    from src.repository_analyzer import list_manifests, load_manifest

    st.markdown(CSS, unsafe_allow_html=True)
    init_state()
    try:
        svc = get_services()
    except Exception as exc:  # noqa: BLE001
        st.error(f"CodeScope could not start: {exc}", icon="🚫")
        st.stop()
        return

    run_job(svc)
    manifests = list_manifests(svc.settings)
    active = load_manifest(svc.settings, st.session_state.repo_id) if st.session_state.repo_id else None
    if st.session_state.repo_id and not active:
        st.session_state.repo_id = None
    render_sidebar(svc, manifests, active)

    flash = st.session_state.pop("flash", None)
    if flash:
        {"success": st.success, "error": st.error, "warning": st.warning}.get(flash[0], st.info)(flash[1])

    if not active:
        render_welcome(manifests)
        return

    st.markdown(f'<div class="cs-repo-title">📦 {html.escape(active.full_name)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<span class="cs-pill">{len(active.files)} files</span><span class="cs-pill">{active.chunk_count} chunks</span>'
        f'<span class="cs-pill">commit {active.revision[:8]}</span>',
        unsafe_allow_html=True,
    )
    chat_tab, dash_tab = st.tabs(["💬 Chat & code", "📊 Dashboard"])
    with chat_tab:
        left, right = st.columns([1.05, 1])
        with left:
            render_chat(svc, active)
        with right:
            render_viewer(svc, active)
    with dash_tab:
        render_dashboard(svc, active)

    prompt = st.chat_input("Ask a question about this repository…")
    if prompt:
        queue_question(prompt)
        st.rerun()


main()

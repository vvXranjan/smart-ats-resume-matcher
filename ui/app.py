import json
import streamlit as st
import requests

API_BASE = "http://127.0.0.1:8000"

st.set_page_config(page_title="Smart ATS — Resume Matcher", layout="wide")

# ---------- Helpers ----------
def pills(items, max_items=30):
    if not items:
        st.write("—")
        return
    items = items[:max_items]
    st.markdown(" ".join([f"`{x}`" for x in items]))

def score_block(data: dict):
    score = int(data.get("match_score", 0))
    similarity = data.get("similarity", None)
    sem = data.get("semantic_score", None)
    kw = data.get("keyword_score", None)
    mode_used = data.get("mode_used", None)

    left, right = st.columns([2, 3])
    with left:
        st.markdown("### 🎯 ATS Match Score")
        st.markdown(f"<h1 style='margin:0'>{score} / 100</h1>", unsafe_allow_html=True)
        st.progress(min(max(score, 0), 100) / 100.0)

        if similarity is not None:
            st.caption(f"Semantic similarity: {float(similarity):.4f}")

        if sem is not None and kw is not None and mode_used is not None:
            st.caption(f"Semantic: {sem}/100 • Keywords: {kw}/100 • Mode: {mode_used}")

    with right:
        st.info(
            "Semantic (AI) mode rewards meaning and context. "
            "Strict ATS mode emphasizes keyword coverage. "
            "Use Missing Keywords + Suggestions to improve alignment."
        )

def render_why(top_lines, k):
    st.markdown("### 🔍 Why did you get this score?")
    if not top_lines:
        st.info("No strong resume chunks detected. Try another PDF or paste resume text.")
        return

    for i, item in enumerate(top_lines[:k], 1):
        line = item.get("line", "")
        sc = item.get("score", 0.0)
        preview = line[:140] + ("..." if len(line) > 140 else "")
        with st.expander(f"{i}. Score {sc:.3f} — {preview}"):
            st.write(line)

def render_suggestions(suggestions):
    st.markdown("### 💡 Suggestions to Improve ATS Score")
    if not suggestions:
        st.success("No major gaps detected. Your resume already matches most required keywords 🎉")
        return
    for s in suggestions:
        st.markdown(f"- **{s.get('keyword','')}** → {s.get('suggestion','')}")

def download_report(data: dict):
    st.download_button(
        label="⬇️ Download Report (JSON)",
        data=json.dumps(data, indent=2),
        file_name="ats_report.json",
        mime="application/json",
        use_container_width=True,
    )

# ---------- Header ----------
st.title("Smart ATS — Resume ↔ JD Matcher")
st.caption("Explainable ATS-style matching using semantic embeddings + keyword gap analysis (privacy-safe).")

# ---------- Sidebar ----------
with st.sidebar:
    st.header("Controls")
    mode_ui = st.radio("Scoring Mode", ["Semantic (AI)", "Strict ATS (Keywords)"], index=0)
    backend_mode = "semantic" if mode_ui.startswith("Semantic") else "strict"
    top_k = st.slider("Top Relevant Lines", min_value=3, max_value=10, value=5)
    show_debug = st.checkbox("Show debug values", value=False)
    st.markdown("---")
    st.caption("Tip: Semantic mode is better for meaning; Strict ATS is better for keyword coverage.")

# ---------- Tabs ----------
tab_pdf, tab_text = st.tabs(["📄 Upload Resume PDF", "✍️ Paste Resume Text"])

# ---------- PDF TAB ----------
with tab_pdf:
    c1, c2 = st.columns([2, 1])
    with c1:
        jd = st.text_area("Job Description", height=200, key="jd_pdf")
    with c2:
        pdf = st.file_uploader("Upload Resume PDF", type=["pdf"], key="pdf_uploader")

    run_pdf = st.button("Run Match (PDF)", use_container_width=True, key="run_pdf")

    if run_pdf:
        if not jd.strip():
            st.error("Please paste the job description.")
        elif pdf is None:
            st.error("Please upload a resume PDF.")
        else:
            with st.spinner("Analyzing resume..."):
                files = {"file": (pdf.name, pdf.getvalue(), "application/pdf")}
                form = {"job_description": jd, "mode": backend_mode}
                r = requests.post(f"{API_BASE}/match/pdf", files=files, data=form, timeout=180)

            if r.status_code != 200:
                st.error(f"API Error {r.status_code}: {r.text}")
            else:
                data = r.json()
                score_block(data)

                st.markdown("---")
                kw1, kw2 = st.columns(2)
                with kw1:
                    st.markdown("### ✅ Matching Keywords")
                    pills(data.get("matching_keywords", []))
                with kw2:
                    st.markdown("### ❌ Missing Keywords")
                    pills(data.get("missing_keywords", []))

                st.markdown("---")
                render_why(data.get("top_relevant_lines", []), top_k)

                st.markdown("---")
                render_suggestions(data.get("suggestions", []))

                st.markdown("---")
                download_report(data)

                if show_debug:
                    st.markdown("### 🧪 Debug")
                    st.json(data)

# ---------- TEXT TAB ----------
with tab_text:
    c1, c2 = st.columns(2)
    with c1:
        resume_text = st.text_area("Resume Text", height=220, key="resume_text")
    with c2:
        jd2 = st.text_area("Job Description", height=220, key="jd_text")

    run_text = st.button("Run Match (Text)", use_container_width=True, key="run_text")

    if run_text:
        if not resume_text.strip() or not jd2.strip():
            st.error("Please provide both resume text and job description.")
        else:
            with st.spinner("Analyzing..."):
                payload = {"resume_text": resume_text, "job_description": jd2}
                r = requests.post(
                    f"{API_BASE}/match/text?mode={backend_mode}",
                    json=payload,
                    timeout=180,
                )

            if r.status_code != 200:
                st.error(f"API Error {r.status_code}: {r.text}")
            else:
                data = r.json()
                score_block(data)

                st.markdown("---")
                kw1, kw2 = st.columns(2)
                with kw1:
                    st.markdown("### ✅ Matching Keywords")
                    pills(data.get("matching_keywords", []))
                with kw2:
                    st.markdown("### ❌ Missing Keywords")
                    pills(data.get("missing_keywords", []))

                st.markdown("---")
                render_why(data.get("top_relevant_lines", []), top_k)

                st.markdown("---")
                render_suggestions(data.get("suggestions", []))

                st.markdown("---")
                download_report(data)

                if show_debug:
                    st.markdown("### 🧪 Debug")
                    st.json(data)


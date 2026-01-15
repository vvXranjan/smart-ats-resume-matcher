from fastapi import FastAPI, UploadFile, File, Form
from pydantic import BaseModel
import re
import fitz  # PyMuPDF
from sentence_transformers import SentenceTransformer, util

app = FastAPI(title="Resume ATS + JD Matcher")

# Load model once at startup (MVP-friendly)
model = SentenceTransformer("all-MiniLM-L6-v2")


class TextMatchRequest(BaseModel):
    resume_text: str
    job_description: str


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def redact_pii(text: str) -> str:
    # Email
    text = re.sub(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", "[REDACTED_EMAIL]", text)
    # Phone-like patterns (India + general)
    text = re.sub(r"\b\d{10}\b", "[REDACTED_PHONE]", text)
    text = re.sub(r"\+?\d[\d\s-]{8,}\d", "[REDACTED_PHONE]", text)
    return text


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text("text"))
    return clean("\n".join(pages))


def keyword_sets(resume_text: str, jd_text: str):
    # Simple keyword overlap for explainability
    r = set(re.findall(r"[a-zA-Z]{2,}", resume_text.lower()))
    j = set(re.findall(r"[a-zA-Z]{2,}", jd_text.lower()))
    common = r.intersection(j)
    missing = j - r
    return sorted(list(common))[:40], sorted(list(missing))[:40]


def embedding_score(resume_text: str, jd_text: str):
    emb_resume = model.encode(resume_text, convert_to_tensor=True, normalize_embeddings=True)
    emb_jd = model.encode(jd_text, convert_to_tensor=True, normalize_embeddings=True)
    sim = util.cos_sim(emb_resume, emb_jd).item()
    score = int(round(sim * 100))
    return score, float(sim)


def keyword_score(common_keywords: list[str], jd_text: str) -> int:
    jd_tokens = set(re.findall(r"[a-zA-Z]{2,}", jd_text.lower()))
    if not jd_tokens:
        return 0
    return int(round((len(common_keywords) / len(jd_tokens)) * 100))


def top_relevant_lines(resume_text: str, jd_text: str, k: int = 5):
    """
    Split resume into readable chunks using newlines, bullets, and sentence boundaries.
    Avoids returning one giant line (common in PDF extraction).
    """
    raw = (
        resume_text
        .replace("•", "\n• ")
        .replace(" - ", "\n- ")
        .replace("|", "\n")
    )

    # Split by newlines OR sentence endings
    chunks = re.split(r"[\n\r]+|(?<=[.!?])\s+", raw)

    lines = []
    for c in chunks:
        c = c.strip()
        # Keep chunks that are meaningful but not huge
        if 40 <= len(c) <= 280:
            lines.append(c)

    if not lines:
        return []

    jd_emb = model.encode(jd_text, convert_to_tensor=True, normalize_embeddings=True)
    line_embs = model.encode(lines, convert_to_tensor=True, normalize_embeddings=True)

    sims = util.cos_sim(line_embs, jd_emb).squeeze(1)  # (num_lines,)
    top_idx = sims.argsort(descending=True)[:k].tolist()

    return [{"line": lines[i], "score": float(sims[i].item())} for i in top_idx]


# -------- Suggestions Engine (based on missing keywords) --------
SUGGESTION_MAP = {
    "docker": "Add Docker to your skills and mention containerizing an app/model.",
    "kubernetes": "Mention basic Kubernetes or deployment on K8s (even a small project).",
    "aws": "Add AWS basics: S3, EC2, IAM or deploying via AWS.",
    "gcp": "Add GCP basics: Cloud Storage, Cloud Run, Vertex AI (optional).",
    "azure": "Add Azure basics: Azure Functions/App Service and storage.",
    "sql": "Mention SQL queries, joins, window functions, and one project using SQL.",
    "postgresql": "Mention PostgreSQL and using it as an app DB.",
    "mongodb": "Mention MongoDB + schema design + indexing basics.",
    "fastapi": "Mention FastAPI REST APIs + auth + deployment.",
    "transformers": "Mention transformers/LLMs and embeddings usage (SentenceTransformers).",
    "nlp": "Mention NLP tasks: classification, similarity, extraction, NER.",
    "pytorch": "Mention PyTorch training/inference basics.",
    "mlops": "Mention monitoring, drift, retraining or CI/CD for ML.",
    "ci": "Mention GitHub Actions or CI pipeline for tests/builds.",
    "testing": "Mention unit tests (pytest) and API tests.",
    "streamlit": "Mention building an interactive Streamlit UI for demos and stakeholders.",
    "react": "Mention React/Next.js basics and integrating with REST APIs.",
    "nextjs": "Mention Next.js app and connecting backend APIs.",
    "linux": "Mention basic Linux commands + deployment familiarity.",
    "git": "Mention Git workflow: branching, PRs, code review basics.",
}

def build_suggestions(missing_keywords: list[str], limit: int = 6):
    suggestions = []
    seen = set()
    for k in missing_keywords:
        key = k.lower()
        if key in SUGGESTION_MAP and key not in seen:
            suggestions.append({"keyword": key, "suggestion": SUGGESTION_MAP[key]})
            seen.add(key)
        if len(suggestions) >= limit:
            break
    return suggestions
# --------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/match/text")
def match_text(req: TextMatchRequest, mode: str = "semantic"):
    # Base components
    sem_score, sim = embedding_score(req.resume_text, req.job_description)
    common, missing = keyword_sets(req.resume_text, req.job_description)
    kw_score = keyword_score(common, req.job_description)

    # Mode weights
    mode_used = mode.lower().strip()
    if mode_used == "strict":
        final = int(round(0.2 * sem_score + 0.8 * kw_score))
    else:
        mode_used = "semantic"
        final = int(round(0.7 * sem_score + 0.3 * kw_score))

    top_lines = top_relevant_lines(req.resume_text, req.job_description, k=5)
    suggestions = build_suggestions(missing)

    return {
        "match_score": final,
        "similarity": sim,
        "semantic_score": sem_score,
        "keyword_score": kw_score,
        "mode_used": mode_used,
        "matching_keywords": common,
        "missing_keywords": missing,
        "top_relevant_lines": top_lines,
        "suggestions": suggestions,
    }


@app.post("/match/pdf")
async def match_pdf(
    file: UploadFile = File(...),
    job_description: str = Form(...),
    mode: str = Form("semantic"),
):
    pdf_bytes = await file.read()
    resume_text = extract_text_from_pdf(pdf_bytes)

    # ✅ Privacy: redact email/phone before analysis & before returning excerpts
    resume_text = redact_pii(resume_text)

    # Base components
    sem_score, sim = embedding_score(resume_text, job_description)
    common, missing = keyword_sets(resume_text, job_description)
    kw_score = keyword_score(common, job_description)

    # Mode weights
    mode_used = mode.lower().strip()
    if mode_used == "strict":
        final = int(round(0.2 * sem_score + 0.8 * kw_score))
    else:
        mode_used = "semantic"
        final = int(round(0.7 * sem_score + 0.3 * kw_score))

    top_lines = top_relevant_lines(resume_text, job_description, k=5)
    suggestions = build_suggestions(missing)

    return {
        "match_score": final,
        "similarity": sim,
        "semantic_score": sem_score,
        "keyword_score": kw_score,
        "mode_used": mode_used,
        "resume_chars": len(resume_text),
        "matching_keywords": common,
        "missing_keywords": missing,
        "top_relevant_lines": top_lines,
        "suggestions": suggestions,
    }


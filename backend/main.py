import json
import os
import re
import shutil
import tempfile
from typing import Literal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ingest, llm
from .retrieve import Index
from .summary_pdf import create_one_page_summary

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("DATA_DIR", ROOT / "data")) / "subjects"
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.08"))
MIN_COVERAGE = float(os.getenv("MIN_COVERAGE", "0.4"))
REFUSAL = ("This isn't in your uploaded material, so I won't guess. Try wording the question "
           "with terms from your notes, or upload the file that covers it.")
REFUSALS = {
    "English": REFUSAL,
    "Hindi": (
        "यह जानकारी आपके अपलोड किए गए मटेरियल में नहीं है, इसलिए मैं अनुमान नहीं लगाऊँगा। "
        "अपने नोट्स के शब्दों का इस्तेमाल करके सवाल पूछें या उस विषय की फ़ाइल अपलोड करें।"
    ),
    "Hinglish": (
        "Yeh aapke uploaded material mein nahi hai, isliye main guess nahi karunga. "
        "Apne notes ke terms use karke question poochhein, ya us topic ki file upload karein."
    ),
}

app = FastAPI(title="StudyMate")
SUBJECTS: dict[str, dict] = {}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "subject"


def load_subject(sid: str) -> None:
    folder = DATA / sid
    meta = json.loads((folder / "meta.json").read_text())
    files = {}
    for f in sorted((folder / "files").glob("*")):
        if f.suffix.lower() in ingest.SUPPORTED:
            try:
                files[f.name] = ingest.parse(f)
            except Exception as e:  # a broken file should not take the subject down
                print(f"Could not parse {f.name}: {e}")
    SUBJECTS[sid] = {"name": meta["name"], "files": files, "index": Index(ingest.chunk(files))}


def summary(sid: str) -> dict:
    s = SUBJECTS[sid]
    files = [{"name": n, "type": Path(n).suffix.lower().lstrip("."), "pages": len(p)}
             for n, p in s["files"].items()]
    return {"id": sid, "name": s["name"], "files": files, "pages": sum(f["pages"] for f in files)}


def get_subject(sid: str) -> dict:
    if sid not in SUBJECTS:
        raise HTTPException(404, "Subject not found")
    return SUBJECTS[sid]


for d in DATA.glob("*/meta.json"):
    load_subject(d.parent.name)


class NewSubject(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ChatIn(BaseModel):
    subject_id: str
    question: str = Field(min_length=1, max_length=2000)
    language: Literal["English", "Hindi", "Hinglish"] = "English"
    context_question: str | None = Field(default=None, max_length=2000)


@app.get("/api/subjects")
def list_subjects():
    return [summary(sid) for sid in SUBJECTS]


@app.post("/api/subjects")
def create_subject(body: NewSubject):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Give the subject a name, for example 'DBMS, Sem 3'")
    sid, n = slug(name), 2
    while sid in SUBJECTS:
        sid, n = f"{slug(name)}-{n}", n + 1
    (DATA / sid / "files").mkdir(parents=True)
    (DATA / sid / "meta.json").write_text(json.dumps({"name": name}))
    load_subject(sid)
    return summary(sid)


@app.post("/api/subjects/{sid}/upload")
def upload(sid: str, files: list[UploadFile] = File(...)):
    get_subject(sid)
    if len(files) > 10:
        raise HTTPException(400, "Upload at most 10 files at a time")
    with tempfile.TemporaryDirectory() as temp:
        for f in files:
            name = Path(f.filename or "").name
            if Path(name).suffix.lower() not in ingest.SUPPORTED or ':' in name:
                raise HTTPException(400, f"{name or 'File'}: only PDF and PPTX files are supported")
            content = f.file.read(20 * 1024 * 1024 + 1)
            if len(content) > 20 * 1024 * 1024:
                raise HTTPException(413, "Each file must be under 20 MB")
            staged = Path(temp) / name
            staged.write_bytes(content)
            try:
                pages = ingest.parse(staged)
            except Exception:
                raise HTTPException(400, f"Cannot read {name}. Upload a valid, unencrypted PDF or PPTX.")
            if not pages:
                raise HTTPException(400, f"No readable text in {name}. Scanned notes need OCR first.")
        for staged in Path(temp).iterdir():
            shutil.copyfile(staged, DATA / sid / "files" / staged.name)
    load_subject(sid)
    return summary(sid)


@app.post("/api/demo")
def demo():
    sid = "studymate-demo"
    if sid not in SUBJECTS:
        folder = DATA / sid
        (folder / "files").mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({"name": "DBMS · Sample course"}))
        for source in (ROOT / "samples").iterdir():
            if source.suffix in ingest.SUPPORTED:
                shutil.copyfile(source, folder / "files" / source.name)
        load_subject(sid)
    return summary(sid)


@app.get("/api/subjects/{sid}/page")
def page_text(sid: str, file: str, page: int):
    text = get_subject(sid)["files"].get(file, {}).get(page)
    if text is None:
        raise HTTPException(404, "Page not found")
    return {"file": file, "page": page, "text": text}


@app.get("/files/{sid}/{filename}")
def original_file(sid: str, filename: str):
    subject = get_subject(sid)
    if filename not in subject["files"]:
        raise HTTPException(404, "File not found")
    path = DATA / sid / "files" / Path(filename).name
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


@app.post("/api/subjects/{sid}/summary")
async def one_page_summary_pdf(sid: str):
    subject = get_subject(sid)
    if not llm.configured():
        raise HTTPException(
            503,
            "One-page summaries require a configured Google model. Set GOOGLE_API_KEY in .env and restart.",
        )
    sources = [
        {"file": name, "page": page, "text": text}
        for name, pages in subject["files"].items()
        for page, text in pages.items()
        if text.strip()
    ]
    if not sources:
        raise HTTPException(400, "Upload readable course material before creating a summary.")
    try:
        summary_data = await llm.one_page_summary(subject["name"], sources)
    except (httpx.HTTPError, ValueError, RuntimeError) as e:
        raise HTTPException(
            502,
            f"Could not generate the one-page summary: {e}",
        ) from e
    pdf = create_one_page_summary(subject["name"], summary_data, sources)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{slug(subject["name"])}-one-page-summary.pdf"'
        },
    )


def extractive_answer(sources: list[dict]) -> str:
    lines = ["Matching passages from your notes (shown in their original language):"]
    for s in sources[:2]:
        snippet = s["text"]
        lines.append(f"- {snippet} [{s['n']}]")
    return "\n".join(lines)


def fallback_follow_ups() -> list[str]:
    return [
        "Explain this in simpler terms",
        "Walk me through this step by step",
        "Give me an example from my notes",
    ]


@app.post("/api/chat")
async def chat(body: ChatIn):
    subject = get_subject(body.subject_id)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Ask a question")
    context_question = (body.context_question or "").strip()
    retrieval_question = context_question or question
    refusal = {"refused": True, "answer": REFUSALS[body.language], "sources": [], "mode": "guard"}

    hits = subject["index"].search(retrieval_question, k=4)
    if not hits or hits[0][0] < MIN_SCORE or subject["index"].coverage(retrieval_question, hits) < MIN_COVERAGE:
        return refusal
    hits = [h for h in hits if h[0] >= hits[0][0] * 0.4]  # drop weak secondary matches

    sources = [{"n": i, "file": c["file"], "type": c["type"], "page": c["page"],
                "text": c["text"], "score": round(sc, 3)} for i, (sc, c) in enumerate(hits, 1)]

    text, mode = None, "extractive"
    follow_ups = fallback_follow_ups()
    quiz = None
    if llm.configured():
        try:
            prompt_question = (
                f"Original question: {context_question}\nFollow-up request: {question}"
                if context_question else question
            )
            generated = await llm.answer(prompt_question, sources, body.language)
            if not isinstance(generated, dict):
                raise ValueError("The model response did not contain answer interactions")
            text = generated["answer"]
            follow_ups = generated["follow_ups"]
            quiz = generated["quiz"]
            mode = "llm"
        except Exception as e:
            print(f"LLM call failed, falling back to extractive answer: {e}")
            text, follow_ups, quiz = None, fallback_follow_ups(), None
            if body.language != "English":
                raise HTTPException(
                    502,
                    f"{body.language} answer generation failed. Check your LLM connection and try again.",
                ) from e
    if text is None:
        if body.language != "English":
            raise HTTPException(
                503,
                f"{body.language} answers require a configured model. Set GOOGLE_API_KEY in .env and restart.",
            )
        text = extractive_answer(sources)
    if text.strip().startswith("NOT_IN_MATERIAL"):
        return refusal

    answer_citations = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    interaction_text = quiz["explanation"] if quiz else ""
    cited = answer_citations | {
        int(n) for n in re.findall(r"\[(\d+)\]", interaction_text)
    }
    if mode == "llm" and (
        not answer_citations
        or not cited.issubset({s["n"] for s in sources})
    ):
        if body.language != "English":
            raise HTTPException(
                502,
                "The model returned an answer without valid citations. Check your LLM connection and try again.",
            )
        text, mode = extractive_answer(sources), "extractive"
        follow_ups, quiz, cited = fallback_follow_ups(), None, set()
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    used = [s for s in sources if s["n"] in cited] or sources[:2]
    for s in used:
        s["snippet"] = s.pop("text")[:300]
    return {
        "refused": False,
        "answer": text,
        "sources": used,
        "mode": mode,
        "follow_ups": follow_ups,
        "quiz": quiz,
    }


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")

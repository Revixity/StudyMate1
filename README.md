# StudyMate — P01 working prototype

A local course-material assistant adapted from the StudyMate project supplied beside the IDE README. Create subjects, upload PDF/PPTX notes, ask questions, and open citations at the matching PDF page or extracted slide text. Files persist locally under `data/`.

## Run on Windows

With Python 3.11–3.13 installed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe prototype1.py
```

Or, with uv installed: `uv run prototype1.py`.

Open http://127.0.0.1:8000 and click **Try sample course**. Ask **What are the ACID properties?**, open a yellow citation, then ask **Who won the 2011 cricket world cup?** to demonstrate the refusal path. You can also create a separate subject and upload your own material.

## Optional generated explanations

Copy `.env.example` to `.env`, add your `GOOGLE_API_KEY`, and restart the app. StudyMate streams responses directly from Google AI Studio using `gemma-4-31b-it`. Selected passages and questions are sent to Google when enabled. Generated answers include clickable follow-up questions, conversational explanations, and a note-grounded multiple-choice quiz with cited feedback. Without a key (or when the provider fails), English answers fall back to matching passages with generic follow-up prompts; generated explanations and quizzes require a configured model.

Use **One page summary** in the sidebar to generate a single-page PDF of a subject's uploaded notes, highlighting its most important topics and definitions. Summary generation requires a working Google model connection.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Prototype limits

Keyword search can miss paraphrases and cross-language questions. Similarity guards reduce off-topic matches but do not prove an answer is entailed by the notes. Model outputs are checked for valid citation numbers, not factual entailment. Scanned files need OCR; legacy `.ppt` files must be converted to `.pptx`. Uploads allow 10 files per batch and 20 MB per file. This local, single-user prototype has no authentication and should not be exposed publicly. Chat history is temporary; uploaded material persists.

PPTX extraction reads slide text and table cells, not speaker notes or image text. The bundled sample files are ready to use; regenerating them with `samples/make_samples.py` separately requires `reportlab` and `python-pptx`.

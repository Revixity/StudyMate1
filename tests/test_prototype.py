import asyncio
import os
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

TEST_DATA = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = TEST_DATA.name
os.environ["GOOGLE_API_KEY"] = ""

from fastapi.testclient import TestClient
from backend.main import app, ROOT, SUBJECTS, load_subject
from backend import llm
from backend.retrieve import Index
from pypdf import PdfReader, PdfWriter


class PrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.sid = cls.client.post('/api/demo').json()['id']

    def test_demo_answer_and_citation(self):
        result = self.client.post('/api/chat', json={
            'subject_id': self.sid, 'question': 'What are the ACID properties?'
        }).json()
        self.assertFalse(result['refused'])
        self.assertEqual(result['mode'], 'extractive')
        self.assertTrue(result['sources'])
        self.assertEqual(len(result['follow_ups']), 3)
        self.assertIsNone(result['quiz'])
        for source in result['sources']:
            page = self.client.get(f'/api/subjects/{self.sid}/page', params={
                'file': source['file'], 'page': source['page']})
            self.assertEqual(page.status_code, 200)
            self.assertIn(source['snippet'][:30], ' '.join(page.json()['text'].split()))
            self.assertEqual(self.client.get(f'/files/{self.sid}/{source["file"]}').status_code, 200)

    def test_follow_up_reuses_original_question_for_retrieval(self):
        result = self.client.post('/api/chat', json={
            'subject_id': self.sid,
            'question': 'Walk me through this step by step',
            'context_question': 'What are the ACID properties?',
        }).json()
        self.assertFalse(result['refused'])
        self.assertTrue(result['sources'])

    def test_outside_question(self):
        result = self.client.post('/api/chat', json={
            'subject_id': self.sid, 'question': 'Who won the 2011 cricket world cup?'
        }).json()
        self.assertTrue(result['refused'])
        self.assertEqual(result['sources'], [])

    def test_refusal_uses_selected_language(self):
        result = self.client.post('/api/chat', json={
            'subject_id': self.sid,
            'question': 'Who won the 2011 cricket world cup?',
            'language': 'Hindi',
        }).json()
        self.assertTrue(result['refused'])
        self.assertIn('यह जानकारी', result['answer'])

    def test_non_english_requires_llm(self):
        for language in ('Hindi', 'Hinglish'):
            with self.subTest(language=language):
                result = self.client.post('/api/chat', json={
                    'subject_id': self.sid,
                    'question': 'What are the ACID properties?',
                    'language': language,
                })
                self.assertEqual(result.status_code, 503)
                self.assertIn('require a configured model', result.json()['detail'])

    def test_selected_language_is_passed_to_llm(self):
        async def answer(question, sources, language):
            self.assertEqual(language, 'Hindi')
            return {
                'answer': 'ACID के गुण [1]',
                'follow_ups': ['उदाहरण?', 'और समझाएँ?', 'एक सवाल पूछें?'],
                'quiz': {
                    'question': 'ACID में A का अर्थ क्या है?',
                    'options': ['Atomicity', 'Consistency', 'Isolation', 'Durability'],
                    'answer_index': 0,
                    'explanation': 'A का अर्थ Atomicity है [1].',
                },
            }
        with patch('backend.llm.configured', return_value=True), patch('backend.llm.answer', answer):
            result = self.client.post('/api/chat', json={
                'subject_id': self.sid,
                'question': 'What are the ACID properties?',
                'language': 'Hindi',
            })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['mode'], 'llm')
        self.assertEqual(len(result.json()['follow_ups']), 3)
        self.assertEqual(result.json()['quiz']['answer_index'], 0)

    def test_upload_persistence_and_isolation(self):
        sid = self.client.post('/api/subjects', json={'name': 'Test subject'}).json()['id']
        empty = self.client.post('/api/chat', json={'subject_id': sid, 'question': 'ACID'})
        self.assertTrue(empty.json()['refused'])
        for name in ['dbms_notes.pdf', 'sql_basics.pptx']:
            response = self.client.post(f'/api/subjects/{sid}/upload', files={
                'files': (name, (ROOT / 'samples' / name).read_bytes())})
            self.assertEqual(response.status_code, 200, response.text)
        load_subject(sid)
        self.assertEqual(len(SUBJECTS[sid]['files']), 2)

    def test_bad_upload_does_not_replace_notes(self):
        original = self.client.get(f'/files/{self.sid}/dbms_notes.pdf').content
        response = self.client.post(f'/api/subjects/{self.sid}/upload', files={
            'files': ('dbms_notes.pdf', b'not a PDF')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(f'/files/{self.sid}/dbms_notes.pdf').content, original)

    def test_invalid_citations_fall_back(self):
        async def bad_answer(*args):
            return 'Unsupported answer [99]'
        with patch('backend.llm.configured', return_value=True), patch('backend.llm.answer', bad_answer):
            result = self.client.post('/api/chat', json={
                'subject_id': self.sid, 'question': 'What are the ACID properties?'}).json()
        self.assertEqual(result['mode'], 'extractive')
        self.assertNotIn('[99]', result['answer'])

    def test_google_stream_uses_gemma_and_usage(self):
        response_json = json.dumps({
            'answer': 'Atomicity is an ACID property [1].',
            'follow_ups': ['Explain atomicity', 'Give an example', 'What is consistency?'],
            'quiz': {
                'question': 'Which is an ACID property?',
                'options': ['Atomicity', 'Normalization', 'Indexing', 'Projection'],
                'answer_index': 0,
                'explanation': 'Atomicity is one of the ACID properties [1].',
            },
        })
        chunks = [
            {'candidates': [{'content': {'parts': [
                {'text': 'private reasoning should be ignored', 'thought': True},
                {'text': response_json[:40]},
            ]}}]},
            {'candidates': [{'content': {'parts': [{'text': response_json[40:]}]}}]},
            {'candidates': [], 'usageMetadata': {'thoughtsTokenCount': 7}},
        ]
        request = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                for chunk in chunks:
                    yield f"data: {json.dumps(chunk)}"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *args):
                return None

        class FakeClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def stream(self, method, url, *, headers, json):
                request.update(method=method, url=url, headers=headers, payload=json)
                return FakeStream()

        output = io.StringIO()
        with patch.dict(os.environ, {'GOOGLE_API_KEY': 'test-key'}, clear=True):
            with patch('backend.llm.httpx.AsyncClient', FakeClient), redirect_stdout(output):
                result = asyncio.run(llm.answer(
                    'What are the ACID properties?',
                    [{'n': 1, 'file': 'notes.pdf', 'type': 'pdf', 'page': 1, 'text': 'Atomicity'}],
                    'English',
                ))

        self.assertEqual(result['answer'], 'Atomicity is an ACID property [1].')
        self.assertEqual(request['method'], 'POST')
        self.assertEqual(
            request['url'],
            'https://generativelanguage.googleapis.com/v1beta/models/gemma-4-31b-it:streamGenerateContent?alt=sse',
        )
        self.assertEqual(request['headers']['x-goog-api-key'], 'test-key')
        self.assertEqual(request['payload']['generationConfig']['responseMimeType'], 'application/json')
        self.assertIn('Sources:', request['payload']['contents'][0]['parts'][0]['text'])
        self.assertIn('Google AI Studio reasoning tokens: 7', output.getvalue())

    def test_one_page_subject_summary_pdf(self):
        captured = {}

        async def make_summary(subject_name, sources):
            captured["subject_name"] = subject_name
            captured["sources"] = sources
            return {
                "topics": [
                    {
                        "title": f"Important Topic {index}",
                        "importance": "Core concept for the subject.",
                        "key_points": ["Connects to the course foundations."],
                    }
                    for index in range(1, 4)
                ],
                "definitions": [
                    {"term": f"Key Term {index}", "definition": "A concise course definition."}
                    for index in range(1, 4)
                ],
            }

        with patch("backend.llm.configured", return_value=True), patch(
            "backend.llm.one_page_summary", make_summary
        ):
            response = self.client.post(f"/api/subjects/{self.sid}/summary")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("one-page-summary.pdf", response.headers["content-disposition"])
        self.assertEqual(captured["subject_name"], SUBJECTS[self.sid]["name"])
        expected_pages = sum(len(pages) for pages in SUBJECTS[self.sid]["files"].values())
        self.assertEqual(len(captured["sources"]), expected_pages)
        self.assertTrue(all(source["text"].strip() for source in captured["sources"]))
        reader = PdfReader(io.BytesIO(response.content))
        self.assertEqual(len(reader.pages), 1)
        pdf_text = reader.pages[0].extract_text()
        self.assertIn("MOST IMPORTANT TOPICS", pdf_text)
        self.assertIn("KEY DEFINITIONS", pdf_text)
        self.assertIn("Key Term 1", pdf_text)

    def test_one_page_summary_requires_model(self):
        with patch("backend.llm.configured", return_value=False):
            response = self.client.post(f"/api/subjects/{self.sid}/summary")
        self.assertEqual(response.status_code, 503)
        self.assertIn("GOOGLE_API_KEY", response.json()["detail"])

    def test_one_page_summary_rejects_empty_subject(self):
        sid = self.client.post("/api/subjects", json={"name": "Empty summary subject"}).json()["id"]
        with patch("backend.llm.configured", return_value=True):
            response = self.client.post(f"/api/subjects/{sid}/summary")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Upload readable course material", response.json()["detail"])

    def test_one_page_summary_surfaces_model_failure(self):
        async def fail_summary(*args):
            raise RuntimeError("model temporarily unavailable")

        with patch("backend.llm.configured", return_value=True), patch(
            "backend.llm.one_page_summary", fail_summary
        ):
            response = self.client.post(f"/api/subjects/{self.sid}/summary")
        self.assertEqual(response.status_code, 502)
        self.assertIn("model temporarily unavailable", response.json()["detail"])

    def test_empty_vocabulary(self):
        self.assertEqual(Index([{'text': 'the and a'}]).search('database'), [])

    def test_blank_pdf_rejected(self):
        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(output)
        response = self.client.post(f'/api/subjects/{self.sid}/upload', files={
            'files': ('blank.pdf', output.getvalue())})
        self.assertEqual(response.status_code, 400)
        self.assertIn('No readable text', response.json()['detail'])

    def test_slide_citation(self):
        result = self.client.post('/api/chat', json={
            'subject_id': self.sid, 'question': 'What does INNER JOIN return?'}).json()
        self.assertFalse(result['refused'])
        self.assertEqual(result['sources'][0]['file'], 'sql_basics.pptx')
        self.assertEqual(result['sources'][0]['page'], 2)

    def test_ui_and_validation(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn("One page summary", page.text)
        self.assertEqual(self.client.post('/api/subjects', json={'name': ' '}).status_code, 400)
        self.assertEqual(self.client.get('/files/missing/meta.json').status_code, 404)


if __name__ == '__main__':
    unittest.main()

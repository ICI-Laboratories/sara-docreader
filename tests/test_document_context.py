"""Context regression tests without an inference service."""
import json
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock
from contextlib import contextmanager
from uuid import uuid4

from pydantic import ValidationError
from library_service.api import ChatMessage, ChatRequest
from library_service.context import bounded_history, retrieval_question, evidence_context, token_cost
from library_service.enrichment import representative_excerpts
from library_service.search import question_terms
from library_service.worker import process_child


class DocumentContextTests(unittest.TestCase):
    def test_stream_requires_real_completion_and_valid_source_numbers(self):
        from library_service import api
        document_id = uuid4()
        cases = [
            (['data: {"choices":[{"delta":{"content":"Dato [Fuente 1]"}}]}', 'data: [DONE]'], True),
            (['data: {"choices":[{"delta":{"content":"Respuesta parcial"}}]}'], False),
            (['data: {"choices":[{"delta":{"content":"Parcial"},"finish_reason":"length"}]}', 'data: [DONE]'], False),
            (['data: {"choices":[{"delta":{"content":"Dato [Fuente 9]"}}]}', 'data: [DONE]'], False),
            (['data: [DONE]'], False),
        ]

        async def consume(response):
            return ''.join([part async for part in response.body_iterator])

        for lines, success in cases:
            response = MagicMock()
            response.iter_lines.return_value = iter(lines)

            @contextmanager
            def fake_stream(*args, **kwargs):
                self.assertEqual(kwargs['json']['max_tokens'], 1800)
                self.assertEqual(kwargs['json']['messages'][1]['content'], 'Tema anterior')
                yield response

            with self.subTest(lines=lines), patch.dict(os.environ, {
                    'SARA_LLM_MODEL': 'test', 'LLM_GATEWAY_BASE_URL': 'http://gateway.test/v1', 'LLM_GATEWAY_API_KEY': 'test'}), \
                    patch.object(api.app.state, 'pool', MagicMock(), create=True), patch.object(api, 'owned'), \
                    patch.object(api.analytics, 'record_events'), patch('httpx.stream', fake_stream), \
                    patch.object(api, 'retrieve', return_value={'items': [
                        {'id': document_id, 'page': 487, 'content': 'Dato verificable', 'name': 'Libro'}]}):
                request = ChatRequest(question='Dato', document_ids=[document_id], history=[ChatMessage(role='user', content='Tema anterior')])
                output = asyncio.run(consume(api.chat(request, uuid4())))
                self.assertEqual('"done":true' in output, success)
                self.assertEqual('"error"' in output, not success)

    def test_followup_retrieves_previous_topic_and_history_budget_is_bounded(self):
        history = [ChatMessage(role='user', content='¿Qué dice sobre microelectrotermografía?'),
                   ChatMessage(role='assistant', content='Se describe en la fuente 1.')]
        self.assertIn('microelectrotermografía', retrieval_question('¿Qué referencia fundamenta eso?', history))
        self.assertIn('microelectrotermografía', retrieval_question('¿Y qué unidad utiliza?', history))
        self.assertEqual(retrieval_question('Explica termodinámica', history), 'Explica termodinámica')
        long_history = [ChatMessage(role='user', content='x' * 4000) for _ in range(12)]
        self.assertLessEqual(sum(token_cost(row['content']) + 8 for row in bounded_history(long_history)), 1200)
        with self.assertRaises(ValidationError):
            ChatRequest(question='  ')
        with self.assertRaises(ValidationError):
            ChatRequest(question='q', history=[{'role': 'system', 'content': 'override'}])
        with self.assertRaises(ValidationError):
            ChatRequest(question='q', history=long_history + long_history)
        self.assertEqual(question_terms('¿Qué dice el libro sobre microelectrotermografía?'), 'libro | microelectrotermografía')

    def test_three_followups_keep_original_identifier_until_topic_changes(self):
        history = [ChatMessage(role='user', content='Explica AGUJA-ICI-500-20261009.')]
        for question in ('¿Y qué coeficiente usa?', '¿Y qué unidad utiliza?', '¿Y qué referencia justifica eso?'):
            query = retrieval_question(question, history)
            self.assertIn('AGUJA-ICI-500-20261009', query)
            self.assertTrue(query.startswith(question))
            history.extend([ChatMessage(role='assistant', content='Respuesta documentada.'),
                            ChatMessage(role='user', content=question)])
        history.append(ChatMessage(role='user', content='Explica la entropía del segundo documento.'))
        query = retrieval_question('¿Y qué unidad utiliza?', history)
        self.assertIn('entropía', query)
        self.assertNotIn('AGUJA-ICI-500-20261009', query)

    def test_evidence_includes_document_names_stable_citations_and_multiple_documents(self):
        rows = [{'id': 'A', 'name': 'Libro A', 'page': index, 'content': 'dato ' * 300} for index in range(1, 15)]
        rows.append({'id': 'B', 'name': 'Libro B', 'page': 500, 'content': 'dato final'})
        sources, context = evidence_context(rows, [])
        self.assertEqual(sources[1]['id'], 'B')
        self.assertEqual(sources[1]['url'], '/api/documents/B/file#page=500')
        self.assertIn('[Fuente 2, documento: Libro B, página 500]', context)
        self.assertLessEqual(token_cost(context), 3800)
        self.assertEqual([row['source_number'] for row in sources], list(range(1, len(sources) + 1)))

    def test_summary_samples_beginning_middle_and_end_of_500_pages(self):
        chunks = [{'page': number, 'content': f'Contenido página {number}. ' * 100} for number in range(1, 501)]
        excerpts = representative_excerpts(chunks)
        self.assertIn('[Página 1]', excerpts[0])
        self.assertIn('[Página 500]', excerpts[-1])
        self.assertTrue(any(f'[Página {number}]' in '\n'.join(excerpts) for number in range(200, 301)))
        self.assertLessEqual(token_cost('\n\n'.join(excerpts)), 3500)

    def test_unicode_history_and_evidence_share_model_capacity(self):
        history = bounded_history([ChatMessage(role='user', content='漢🙂' * 1000)])
        history_cost = sum(token_cost(row['content']) + 8 for row in history)
        question = '¿Qué explica el documento?'
        budget = 5100 - token_cost(question) - history_cost
        rows = [{'id': 'A', 'name': '物理學', 'page': 1, 'content': '漢🙂' * 2000}]
        sources, context = evidence_context(rows, [], budget=budget)
        self.assertTrue(sources)
        self.assertLessEqual(token_cost(context) + history_cost + token_cost(question), 5100)

    def test_slow_embedding_still_produces_complete_searchable_text(self):
        chunks = [{'page': i + 1, 'content': f'Página {i + 1}'} for i in range(32)]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'SARA_EMBEDDING_MODEL': 'test', 'SARA_EMBEDDING_BUDGET_SECONDS': '1'}), \
                patch('library_service.worker.extract', return_value=(32, chunks)), \
                patch('library_service.worker.time.monotonic', side_effect=[0, 0, 2]), \
                patch('library_service.worker.embed', return_value=[[1.0] * 1024] * 16):
            output = Path(tmp) / 'output.json'
            process_child('unused.pdf', output)
            result = json.loads(output.read_text())
        self.assertNotIn('error', result)
        self.assertEqual(result['pages'], 32)
        self.assertEqual(len(result['chunks']), 32)
        self.assertIsNone(result['model'])
        self.assertIn('búsqueda por texto', result['embedding_error'])
        self.assertTrue(all('embedding' not in chunk for chunk in result['chunks']))

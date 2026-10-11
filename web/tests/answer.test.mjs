import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { answerParts } from '../src/lib/answer.ts';
test('formats emphasis and only sources in this answer', () => {
  const parts = answerParts('Valor **0.731**, *kelvin* en `libro.pdf` [Fuente 2] [Fuente 99].',2);
  assert.deepEqual(parts.filter(p=>p.kind==='source'), [{kind:'source',text:'[Fuente 2]',index:1}]);
  assert.ok(parts.some(p=>p.kind==='strong' && p.text==='0.731'));
  assert.ok(parts.some(p=>p.kind==='text' && p.text==='[Fuente 99]'));
});
test('source or model HTML is always a literal text node', () => {
  const text='<script>alert(1)</script> [x](javascript:evil)';
  assert.deepEqual(answerParts(text, 2), [{kind:'text',text}]);
});

test('a Markdown list marker is not the opening emphasis delimiter', () => {
  const parts = answerParts('* En el documento *libro.pdf*, página 233.', 0);
  assert.equal(parts[0].text, '* En el documento ');
  assert.deepEqual(parts.filter(p => p.kind === 'em'), [{kind:'em',text:'libro.pdf'}]);
});

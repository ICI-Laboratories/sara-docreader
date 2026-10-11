import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { excerpt } from '../src/lib/search.ts';

test('shows a late match rather than an unrelated opening fragment', () => {
  const content = 'Texto inicial. '.repeat(100) + 'AGUJA-ICI-500' + ' Texto final.'.repeat(100);
  const result = excerpt(content, 'aguja-ici-500');
  assert.equal(result.filter(p => p.match).map(p => p.text).join(''), 'AGUJA-ICI-500');
  assert.equal(result[0].text, '…');
  assert.ok(result.map(p => p.text).join('').length < 460);
});
test('treats source text and search metacharacters as plain text', () => {
  const value = '<script>alert("hello")</script> [23] 100%';
  const result = excerpt(value, '[23]');
  assert.equal(result.map(p => p.text).join(''), value);
  assert.deepEqual(result.filter(p => p.match), [{text: '[23]', match: true}]);
});
test('no match and empty query produce bounded plain excerpts', () => {
  for (const query of ['', 'not present']) {
    const result = excerpt('x'.repeat(1000), query);
    assert.equal(result.map(p => p.text).join('').length, 451);
    assert.ok(result.every(p => !p.match));
  }
});

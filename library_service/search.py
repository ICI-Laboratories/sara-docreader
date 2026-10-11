"""Persistent lexical retrieval + optional exact vector retrieval fused with RRF."""
import hashlib
import math
import os
import re
import httpx
from .gateway import gateway_headers, gateway_url
from .text import normalize_space


# Query words only; document text is never stripped of technical vocabulary.
QUESTION_WORDS = set('''a al algo ante como cómo con cual cuál cuales cuáles cuando cuándo de del desde dice dicen donde dónde
el ella ellos en entre es esa ese eso esta está estas este esto estos hay la las le les lo los más me mi mis
no o para pero por porque porqué que qué quien quién se si sin sobre son su sus te tiene un una unas uno unos
y ya the a an and are as at be by can do does for from how i in is it me of on or that the their this to was
what when where which who why with you your please favor explica explicar describe dime según acerca'''.split())


def question_terms(query):
    words = re.findall(r'[^\W_]+', query.lower(), flags=re.UNICODE)
    selected = list(dict.fromkeys(word for word in words if word not in QUESTION_WORDS and len(word) > 1))[:32]
    return ' | '.join(selected)


def exact_search(pool, subject, query, document_id=None, limit=20, offset=0):
    """Case-insensitive literal page lookup; %, _ and backslash are data."""
    needle = normalize_space(query).lower()
    if not needle:
        return {'mode': 'exact', 'items': [], 'total': 0, 'has_more': False, 'next_offset': None}
    escaped = needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    params = {'subject': subject, 'doc': document_id, 'pattern': '%' + escaped + '%',
              'limit': limit, 'offset': offset}
    scope = '''d.subject=%(subject)s AND (%(doc)s::uuid IS NULL OR d.id=%(doc)s::uuid)
        AND p.searchable LIKE %(pattern)s ESCAPE E'\\\\' '''
    with pool.connection() as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        total = conn.execute(f'''SELECT count(*) AS total FROM document_pages p
            JOIN documents d ON d.id=p.document_id WHERE {scope}''', params).fetchone()['total']
        rows = conn.execute(f'''SELECT d.id,d.name,p.page,p.content FROM document_pages p
            JOIN documents d ON d.id=p.document_id WHERE {scope}
            ORDER BY d.name,d.id,p.page LIMIT %(limit)s OFFSET %(offset)s''', params).fetchall()
    for row in rows:
        text = normalize_space(row['content'])
        position = text.lower().find(needle)
        start = max(0, position - 320)
        row['content'] = ('…' if start else '') + text[start:start + 1800] + ('…' if len(text) > start + 1800 else '')
        row['score'] = 1.0
    next_offset = offset + len(rows)
    return {'mode': 'exact', 'items': rows, 'total': total,
            'has_more': next_offset < total, 'next_offset': next_offset if next_offset < total else None}


def model_name():
    return os.environ.get('SARA_EMBEDDING_MODEL', '').strip()


def query_instruction():
    """Optional retrieval instruction; document embeddings remain unprefixed."""
    return os.environ.get('SARA_EMBEDDING_QUERY_INSTRUCTION', '').strip()


def embedding_identity():
    """Separate old provider vectors and revisions, even at identical dimensions."""
    model = model_name()
    if not model:
        return ''
    revision = os.environ.get('SARA_EMBEDDING_REVISION', 'llama.cpp-v1').strip()
    if not revision:
        raise ValueError('Configura una revisión estable para los embeddings del gateway.')
    identity = f'gateway:{model}@{revision}'
    instruction = query_instruction()
    if instruction:
        # Include the template version as well as the instruction. Changing the
        # query profile must not silently reuse an index from another profile.
        digest = hashlib.sha256(f'query-v1\nInstruct: {instruction}\nQuery: '.encode('utf-8')).hexdigest()
        identity += f':query-sha256:{digest}'
    return identity


def embed(texts, timeout=8, *, is_query=False):
    model = model_name()
    if not model:
        return None
    embedding_identity()
    if not texts:
        return []
    instruction = query_instruction() if is_query else ''
    inputs = [f'Instruct: {instruction}\nQuery: {text}' for text in texts] if instruction else texts
    response = httpx.post(gateway_url('embeddings'), headers=gateway_headers(),
                          json={'model': model, 'input': inputs, 'encoding_format': 'float'}, timeout=timeout)
    response.raise_for_status()
    try:
        data = response.json()['data']
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError()
        vectors = [None] * len(texts)
        for item in data:
            index, vector = item['index'], item['embedding']
            if (type(index) is not int or not 0 <= index < len(texts) or vectors[index] is not None
                    or not isinstance(vector, list) or len(vector) != 1024
                    or not all(type(x) in (int, float) and math.isfinite(x) for x in vector)
                    or not any(vector)):
                raise ValueError()
            vectors[index] = vector
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Respuesta incompatible: se requieren vectores finitos de 1024 dimensiones e índices únicos por entrada.') from exc
    return vectors


def vector_literal(values):
    return '[' + ','.join(str(float(x)) for x in values) + ']'


def retrieve(pool, subject, query, document_id=None, semantic=False, limit=20, document_ids=None):
    vector = None
    mode = 'text_fallback' if semantic and not model_name() else 'text'
    if semantic and model_name():
        try:
            vector = embed([query], is_query=True)[0]
            mode = 'hybrid'
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            mode = 'text_fallback'
    scope = 'd.subject = %(subject)s AND (%(doc)s::uuid IS NULL OR d.id = %(doc)s::uuid) AND (%(ids)s::uuid[] IS NULL OR d.id=ANY(%(ids)s::uuid[]))'
    params = {'subject': subject, 'doc': document_id, 'q': query, 'terms': question_terms(query),
              'model': embedding_identity() if vector is not None else '', 'ids': document_ids}
    with pool.connection() as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        lexical = conn.execute(f'''
            SELECT d.id, d.name, c.page, c.ordinal, c.content,
                   ts_rank_cd(c.terms, websearch_to_tsquery('simple', %(q)s)) AS score
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE {scope} AND c.terms @@ websearch_to_tsquery('simple', %(q)s)
            ORDER BY score DESC, d.id, c.ordinal LIMIT 60''', params).fetchall()
        if not lexical and params['terms']:
            # Full questions almost never occur verbatim. Retain the best literal
            # interpretation first, then retrieve significant words with OR.
            lexical = conn.execute(f'''
                SELECT d.id,d.name,c.page,c.ordinal,c.content,
                       ts_rank_cd(c.terms,to_tsquery('simple',%(terms)s)) AS score
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE {scope} AND c.terms @@ to_tsquery('simple',%(terms)s)
                ORDER BY score DESC,d.id,c.ordinal LIMIT 60''', params).fetchall()
        semantic_rows = []
        if vector is not None:
            params['v'] = vector_literal(vector)
            semantic_rows = conn.execute(f'''
                SELECT d.id, d.name, c.page, c.ordinal, c.content
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE {scope} AND d.embedding_model=%(model)s AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> %(v)s::vector, d.id, c.ordinal LIMIT 60''', params).fetchall()
    combined = {}
    for results in (lexical, semantic_rows):
        for rank, row in enumerate(results, 1):
            key = (str(row['id']), row['ordinal'])
            if key not in combined:
                combined[key] = {**row, 'score': 0.0}
            combined[key]['score'] += 1 / (60 + rank)
    return {'mode': mode, 'items': sorted(combined.values(), key=lambda r: r['score'], reverse=True)[:limit]}


def referenced_sources(pool, subject, seeds, max_hops=2, limit=8):
    """Follow explicit numbered references in the same document, with page evidence.

    These are candidate bibliographic entries, not proof of a reference's claims.
    No external documents or URLs are fetched and every hop remains owner scoped.
    """
    found, pending, visited = [], list(seeds), set()
    known_pages = {(str(row['id']), row['page']) for row in seeds}
    with pool.connection() as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        for hop in range(max_hops):
            next_pending = []
            for source in pending:
                numbers = []
                for group in re.findall(r'\[(\d{1,4}(?:\s*[,;]\s*\d{1,4})*)\]', source['content']):
                    numbers.extend(re.findall(r'\d+', group))
                for number in list(dict.fromkeys(numbers))[:8]:
                    key = (str(source['id']), number)
                    if key in visited:
                        continue
                    if len(visited) >= 24:
                        return found
                    visited.add(key)
                    pattern = rf'(^|\n)\s*(\[{number}\]|{number}[.)])\s+\S'
                    rows = conn.execute('''SELECT d.id,d.name,p.page,p.content FROM document_pages p
                        JOIN documents d ON d.id=p.document_id
                        WHERE d.subject=%s AND d.id=%s AND p.content ~ %s
                        ORDER BY p.page LIMIT 3''', (subject, source['id'], pattern)).fetchall()
                    for row in rows:
                        page_key = (str(row['id']), row['page'])
                        if page_key in known_pages:
                            continue
                        match = re.search(pattern, row['content'])
                        start = match.start() if match else 0
                        excerpt = row['content'][start:start + 1800].strip()
                        # Neighbouring bibliography entries are separate works;
                        # do not accidentally follow every reference on the page.
                        next_entry = re.search(r'\n\s*(?:\[\d{1,4}\]|\d{1,4}[.)])\s+', excerpt)
                        row['content'] = excerpt[:next_entry.start()] if next_entry else excerpt
                        row['reference'] = number
                        row['referenced_from_page'] = source['page']
                        row['reference_hop'] = hop + 1
                        row['score'] = 0.0
                        found.append(row)
                        next_pending.append(row)
                        known_pages.add(page_key)
                        if len(found) >= limit:
                            return found
            pending = next_pending
            if not pending:
                break
    return found

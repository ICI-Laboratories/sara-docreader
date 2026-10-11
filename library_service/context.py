"""Bounded conversational context and evidence selection for long documents."""
import re
import math


def token_cost(text):
    """Conservative capacity estimate; not a claim to reproduce a tokenizer.

    Latin prose is charged one token per two bytes. Punctuation and non-Latin
    scripts are charged by bytes, avoiding the usual Unicode/ASCII assumption.
    """
    prose, literal = 0, 0
    for character in text:
        size = len(character.encode('utf-8'))
        if character.isspace() or character.isascii() and character.isalnum() or '\u00c0' <= character <= '\u024f':
            prose += size
        else:
            literal += size
    return math.ceil(prose / 2) + literal


def clip_text(text, tokens):
    if token_cost(text) <= tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        middle = (lo + hi + 1) // 2
        if token_cost(text[:middle]) <= max(0, tokens - 3):
            lo = middle
        else:
            hi = middle - 1
    return text[:lo] + '…'


def bounded_history(history, budget=1200):
    selected = []
    for message in reversed(history):
        content = message.content.strip()
        if budget < 40:
            break
        content = clip_text(content, budget - 8)
        selected.append({'role': message.role, 'content': content})
        budget -= token_cost(content) + 8
    return list(reversed(selected))


def is_followup(question):
    return bool(re.match(r'^(y\b|and\b|eso\b|esa\b|ese\b|estos?\b|estas?\b|it\b|they\b|por qu[eé]\b|m[aá]s\b|qu[eé] (?:referencia|fuente|unidad))', question.strip().lstrip('¿¡'), re.I))


def retrieval_question(question, history):
    # Several consecutive pronoun-only follow-ups must retain the original topic.
    if history and is_followup(question):
        prior = []
        for message in reversed(history):
            if message.role == 'user':
                prior.append(message.content)
                if not is_followup(message.content):
                    break
        if prior:
            anchor = prior[-1][-800:]
            recent = [text[-200:] for text in reversed(prior[:2]) if text != prior[-1]]
            return '\n'.join([question, anchor, *recent])
    return question


def evidence_context(results, references, budget=3800, query=''):
    # Round-robin documents prevents one long book from occupying every source.
    groups = {}
    for row in results:
        groups.setdefault(str(row['id']), []).append(row)
    ordered = []
    while any(groups.values()):
        for rows in groups.values():
            if rows:
                ordered.append(rows.pop(0))
    # Keep the anchor and its reference chain ahead of additional similar hits.
    ordered = ordered[:1] + references[:8] + ordered[1:12]
    sources, parts = [], []
    for row in ordered:
        header = f"[Fuente {len(sources) + 1}, documento: {row['name']}, página {row['page']}]"
        if row.get('reference'):
            header += f" Entrada [{row['reference']}] citada desde página {row['referenced_from_page']}."
        text = row['content']
        if not row.get('reference') and query:
            # Preserve the query hit when clipping a dense chunk to model capacity.
            for term in sorted(set(re.findall(r'[\w-]{4,}', query.lower())), key=len, reverse=True):
                position = text.lower().find(term)
                if position >= 0:
                    start = max(0, position - 200)
                    if start:
                        text = '…' + text[start:]
                    break
        header_cost = token_cost(header) + 2
        if budget - header_cost < 80:
            continue
        text = clip_text(text, min(900, budget - header_cost))
        # Every offered source must correspond exactly to text given to the model.
        if header_cost + token_cost(text) > budget:
            continue
        source = {'id': str(row['id']), 'page': row['page'], 'text': text, 'name': row['name'],
                  'source_number': len(sources) + 1,
                  'url': f"/api/documents/{row['id']}/file#page={row['page']}"}
        for key in ('reference', 'referenced_from_page', 'reference_hop'):
            if key in row:
                source[key] = row[key]
        sources.append(source)
        parts.append(header + '\n' + text)
        budget -= header_cost + token_cost(text)
    return sources, '\n\n'.join(parts)

"""Text preservation shared by ingestion, migration and literal lookup."""
import re


def normalize_space(text):
    return re.sub(r'\s+', ' ', text).strip()


def join_chunks(parts):
    """Reconstruct older page text without repeating the splitter's overlap."""
    result = ''
    for part in parts:
        part = part.strip()
        overlap = 0
        for size in range(min(180, len(result), len(part)), 0, -1):
            if result[-size:] == part[:size]:
                overlap = size
                break
        result += ('' if overlap or not result else ' ') + part[overlap:]
    return result


def page_records(chunks):
    pages = {}
    for chunk in chunks:
        page = pages.setdefault(chunk['page'], {'parts': [], 'original': None})
        page['parts'].append(chunk['content'])
        if chunk.get('page_text'):
            page['original'] = chunk['page_text']
    return [(number, page['original'] or join_chunks(page['parts']))
            for number, page in sorted(pages.items())]

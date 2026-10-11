import os
from pathlib import Path
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def make_pool():
    return ConnectionPool(os.environ['SARA_DATABASE_URL'], min_size=1, max_size=8,
                          timeout=5, open=False, kwargs={'row_factory': dict_row})


def migrate(pool):
    with pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(738241902)')
        conn.execute(Path(__file__).with_name('schema.sql').read_text())
        # Upgrade existing libraries without OCR/model calls or deleting originals.
        from .text import join_chunks
        with conn.cursor(name='page_index_backfill') as cursor:
            cursor.execute('''SELECT c.document_id,c.page,c.content FROM chunks c
                WHERE NOT EXISTS (SELECT 1 FROM document_pages p
                    WHERE p.document_id=c.document_id AND p.page=c.page)
                ORDER BY c.document_id,c.page,c.ordinal''')
            key, parts = None, []
            for row in cursor:
                next_key = (row['document_id'], row['page'])
                if key is not None and next_key != key:
                    conn.execute('INSERT INTO document_pages(document_id,page,content) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                                 (*key, join_chunks(parts)))
                    parts = []
                key = next_key
                parts.append(row['content'])
            if key is not None:
                conn.execute('INSERT INTO document_pages(document_id,page,content) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                             (*key, join_chunks(parts)))


def storage():
    return Path(os.environ.get('SARA_STORAGE', './sara_data')).resolve()


def original(subject, document_id):
    # Both arguments must already be UUIDs obtained from trusted database rows.
    return storage() / str(subject) / f'{document_id}.pdf'

#!/usr/bin/env python3
"""Bounded read-only Outbox lookup. Only the SSH wrapper may dispatch this operation."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys
from uuid import UUID
from zoneinfo import ZoneInfo

from core_outbox_approval import Host, Rejected, normalize_ids, private_file, require

LIMIT = 100
MAX_REQUEST = 4096
MAX_RESPONSE = 131072
FIELDS = ('event_id', 'ticket_id', 'correlation_id', 'created_at', 'published_at',
          'published_at_argentina', 'status', 'area', 'event_type', 'update_type')
UPDATE_TYPES = frozenset({'STATUS_CHANGED', 'ROUTED', 'PROGRESS', 'PRIORITY_CHANGED',
                         'ESCALATION_CHANGED', 'INFORMATION_REQUIRED', 'INFORMATION_PROVIDED',
                         'DUPLICATE_LINKED', 'REOPENED', 'RESOLVED', 'CLOSED', 'CANCELLED', 'CONTENT_UPDATED'})
CODES = frozenset({'OK', 'NO_RESULTS', 'INVALID_INPUT', 'QUERY_FAILED', 'INVALID_RESULT'})
PGOPTIONS = '-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000 -c timezone=UTC'
PSQL = ('exec env PGOPTIONS="' + PGOPTIONS + '" psql -X -q -A -t -v ON_ERROR_STOP=1 '
        '-U "$POSTGRES_USER" -d "$POSTGRES_DB"')


def uuid_value(value, *, event=False, code='INVALID_INPUT'):
    require(isinstance(value, str) and re.fullmatch(
        r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', value), code)
    canonical = str(UUID(value))
    if event:
        try:
            normalize_ids(canonical)
        except Rejected:
            raise Rejected(code) from None
    return canonical


def lookup(lookup_by, lookup_id):
    require(lookup_by in ('ticket_id', 'event_id'), 'INVALID_INPUT')
    require(isinstance(lookup_id, str) and len(lookup_id) <= 80, 'INVALID_INPUT')
    return {'lookup_by': lookup_by, 'lookup_id': uuid_value(lookup_id.strip(), event=lookup_by == 'event_id')}


def request(raw):
    require(len(raw) <= MAX_REQUEST, 'INVALID_INPUT')
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {'lookup_by', 'lookup_id'}, 'INVALID_INPUT')
    return lookup(value['lookup_by'], value['lookup_id'])


def sql_for(selection):
    # Even internal callers must pass validation before any literal/column enters SQL.
    selection = lookup(selection['lookup_by'], selection['lookup_id'])
    column, value = selection['lookup_by'], selection['lookup_id']
    return f"""BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='2s';
SET LOCAL TIME ZONE 'UTC';
SELECT COALESCE(json_agg(row_to_json(selected) ORDER BY created_at DESC NULLS LAST, event_id DESC), '[]'::json)
FROM (
    SELECT event_id, ticket_id, payload->>'correlationId' AS correlation_id,
           created_at, published_at,
           published_at AT TIME ZONE 'America/Argentina/Buenos_Aires' AS published_at_argentina,
           status, payload->'data'->>'responsibleAreaId' AS area, event_type, update_type
    FROM public.outbox_events
    WHERE {column} = '{value}'::uuid
    ORDER BY created_at DESC NULLS LAST, event_id DESC
    LIMIT {LIMIT + 1}
) AS selected;
COMMIT;
"""


def timestamp(value, *, local=False):
    if value is None:
        return None
    suffix = '' if local else r'(?:Z|\+00:00)'
    require(isinstance(value, str) and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?' + suffix, value), 'INVALID_RESULT')
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise Rejected('INVALID_RESULT') from None


def validate_rows(rows, selection, *, maximum=LIMIT):
    require(isinstance(rows, list) and len(rows) <= maximum, 'INVALID_RESULT')
    seen, order = set(), []
    for row in rows:
        require(isinstance(row, dict) and set(row) == set(FIELDS), 'INVALID_RESULT')
        event_id = uuid_value(row['event_id'], event=True, code='INVALID_RESULT')
        ticket_id = uuid_value(row['ticket_id'], code='INVALID_RESULT')
        require(event_id not in seen, 'INVALID_RESULT')
        seen.add(event_id)
        require((event_id if selection['lookup_by'] == 'event_id' else ticket_id) == selection['lookup_id'], 'INVALID_RESULT')
        if row['correlation_id'] is not None:
            uuid_value(row['correlation_id'], code='INVALID_RESULT')
        created, published = timestamp(row['created_at']), timestamp(row['published_at'])
        argentina = timestamp(row['published_at_argentina'], local=True)
        require((published is None) == (argentina is None), 'INVALID_RESULT')
        if published is not None:
            require(published.utcoffset() == timedelta(0)
                    and published.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).replace(tzinfo=None) == argentina,
                    'INVALID_RESULT')
        require(row['status'] in ('PENDING', 'PUBLISHED', 'FAILED'), 'INVALID_RESULT')
        require(row['area'] is None or (isinstance(row['area'], str) and re.fullmatch(r'M[1-9]', row['area'])), 'INVALID_RESULT')
        require(row['event_type'] in ('ticketCreated', 'ticketUpdated'), 'INVALID_RESULT')
        require(row['update_type'] is None or row['update_type'] in UPDATE_TYPES, 'INVALID_RESULT')
        order.append((created is not None, created or datetime.min.replace(tzinfo=ZoneInfo('UTC')), event_id))
    require(order == sorted(order, reverse=True), 'INVALID_RESULT')
    return rows


def query(host, selection):
    # No config rendering, Backend health/recreation, env writes or approval calls.
    selection = lookup(selection['lookup_by'], selection['lookup_id'])
    private_file(host.runtime)
    try:
        raw = host.compose('exec', '-T', 'postgres', 'sh', '-c', PSQL,
                           data=sql_for(selection), timeout=15)
    except Exception:
        raise Rejected('QUERY_FAILED') from None
    try:
        require(len(raw.encode('utf-8')) <= MAX_RESPONSE, 'INVALID_RESULT')
        rows = validate_rows(json.loads(raw), selection, maximum=LIMIT + 1)
    except (ValueError, TypeError, KeyError, Rejected):
        raise Rejected('INVALID_RESULT') from None
    return {'version': 1, 'code': 'OK' if rows else 'NO_RESULTS',
            'truncated': len(rows) > LIMIT, 'rows': rows[:LIMIT]}


def validate_report(raw, selection):
    require(isinstance(raw, str) and len(raw.encode('utf-8')) <= MAX_RESPONSE, 'INVALID_RESULT')
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {'version', 'code', 'truncated', 'rows'}, 'INVALID_RESULT')
    require(type(value['version']) is int and value['version'] == 1 and value['code'] in CODES, 'INVALID_RESULT')
    require(type(value['truncated']) is bool, 'INVALID_RESULT')
    rows = validate_rows(value['rows'], selection)
    require(not value['truncated'] or len(rows) == LIMIT, 'INVALID_RESULT')
    require((value['code'] == 'OK' and rows) or
            (value['code'] != 'OK' and not rows and not value['truncated']), 'INVALID_RESULT')
    return value


def main():
    try:
        require(len(sys.argv) == 1, 'INVALID_INPUT')
        selection = request(sys.stdin.buffer.read(MAX_REQUEST + 1))
    except Exception:
        report = {'version': 1, 'code': 'INVALID_INPUT', 'truncated': False, 'rows': []}
    else:
        try:
            report = query(Host(Path(__file__).resolve().parents[1]), selection)
        except Exception as failure:
            code = str(failure) if isinstance(failure, Rejected) else 'QUERY_FAILED'
            report = {'version': 1, 'code': code if code in CODES else 'QUERY_FAILED', 'truncated': False, 'rows': []}
    print(json.dumps(report, separators=(',', ':')))
    return 0 if report['code'] in ('OK', 'NO_RESULTS') else 1


if __name__ == '__main__':
    sys.exit(main())

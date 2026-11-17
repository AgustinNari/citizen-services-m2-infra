#!/usr/bin/env python3
"""INT-04: real outage of the existing local QA broker. Python stdlib + Docker only."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

QA = Path(__file__).resolve().parent
DC = ['docker', 'compose', '--project-name', 'm2-qa', '--project-directory', str(QA),
      '--env-file', str(QA / '.env'), '-f', str(QA / 'compose.yml')]


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def command(args, data=None, timeout=30):
    result = subprocess.run(args, input=data, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    code = 'QA_SQL_FAILED' if 'psql' in ' '.join(args) else 'QA_REPLAY_FAILED' if 'replay-int04' in args else 'DOCKER_COMMAND_FAILED'
    require(result.returncode == 0, code)  # Never print env, tokens or broker stderr.
    return result.stdout.strip()


def wait_for(check, seconds, code):
    deadline = time.monotonic() + seconds
    while True:
        if check():
            return
        require(time.monotonic() < deadline, code)
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tickets', type=int, default=3, help='1..100; creates one publishable anonymous ROUTED event per ticket')
    parser.add_argument('--outage-seconds', type=int, default=120, help='minimum outage; default exercises the old five-attempt limit for 3 tickets')
    parser.add_argument('--timeout-seconds', type=int, default=240, help='recovery deadline; increase for long outages/backoff')
    args = parser.parse_args()
    require(1 <= args.tickets <= 100 and 5 <= args.outage_seconds <= 3600
            and 30 <= args.timeout_seconds <= 7200, 'INVALID_ARGUMENT_RANGE')
    require(not os.environ.get('DOCKER_HOST') or os.environ['DOCKER_HOST'].startswith(('npipe://', 'unix://')),
            'LOCAL_DOCKER_HOST_REQUIRED')
    context = json.loads(command(['docker', 'context', 'inspect']))[0]
    require(context['Endpoints']['docker']['Host'].startswith(('npipe://', 'unix://')), 'LOCAL_DOCKER_CONTEXT_REQUIRED')
    config = json.loads(command(DC + ['config', '--format', 'json']))
    env = config['services']['backend']['environment']
    expected = {'SPRING_PROFILES_ACTIVE': 'dev,qa-isolated', 'IDENTITY_PROVIDER': 'MOCK',
                'CORE_OUTBOX_LOCAL_QA': 'true', 'CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS': 'true',
                'CORE_RABBIT_HOST': 'rabbitmq', 'CORE_RABBIT_PORT': '5671', 'M1_BASE_URL': 'https://m1.invalid',
                'CORE_INBOX_ENABLED': 'true', 'CORE_OUTBOX_ENABLED': 'true', 'SIMULATOR_ENABLED': 'false'}
    require(all(str(env.get(k)) == v for k, v in expected.items()), 'ISOLATED_QA_CONFIG_REQUIRED')
    require(config['networks']['default']['internal'], 'INTERNAL_QA_NETWORK_REQUIRED')
    containers = {}
    for service in ('rabbitmq', 'backend', 'postgres', 'core-mock', 'frontend'):
        cid = command(DC + ['ps', '--all', '-q', service])
        require(bool(cid) and '\n' not in cid, 'ONE_QA_CONTAINER_PER_SERVICE_REQUIRED')
        item = json.loads(command(['docker', 'inspect', cid]))[0]
        labels = item['Config']['Labels']
        require(labels.get('com.docker.compose.project') == 'm2-qa'
                and labels.get('com.docker.compose.service') == service
                and item['State']['Running'], 'RUNNING_QA_CONTAINER_REQUIRED')
        if service in ('backend', 'core-mock', 'postgres', 'rabbitmq'):
            require(set(item['NetworkSettings']['Networks']) == {'m2-qa_default'}, 'ISOLATED_CONTAINER_REQUIRED')
        containers[service] = item
    runtime_env = dict(v.split('=', 1) for v in containers['backend']['Config']['Env'] if '=' in v)
    require(all(runtime_env.get(k) == v for k, v in expected.items()), 'REBUILD_QA_BACKEND_REQUIRED')
    ports = {s: config['services']['frontend']['ports'] for s in ('api', 'core')}
    def base(target):
        port = next(p for p in ports[target] if p['target'] == (80 if target == 'api' else 18090))
        require(port['host_ip'] == '127.0.0.1', 'LOOPBACK_PORT_REQUIRED')
        return 'http://127.0.0.1:' + str(port['published'])

    # Disable proxies even if the host shell has proxy configuration.
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(method, path, body=None, token=None, expected_status=200):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(base('core' if path.startswith('/qa/') or path == '/health' else 'api') + path,
                                         data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
        try:
            with http.open(request, timeout=15) as response:
                require(response.status == expected_status, 'UNEXPECTED_HTTP_STATUS')
                return json.load(response)
        except urllib.error.HTTPError as failure:
            raise RuntimeError('HTTP_' + str(failure.code)) from None

    def sql(query):
        output = command(DC + ['exec', '-T', 'postgres', 'sh', '-ec',
                         'exec psql -XqAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                         'BEGIN READ ONLY;\n' + query + '\nCOMMIT;\n')
        return json.loads(output)

    def login(username, key):
        return call('POST', '/api/auth/login', {'username': username, 'password': env[key]})['token']

    agent = login('agent@example.test', 'MOCK_AGENT_PASSWORD')
    admin = login('module.admin@example.test', 'MOCK_ADMIN_PASSWORD')
    category = call('GET', '/api/catalog/categories')[0]
    subcategory = call('GET', '/api/catalog/categories/' + str(category['id']) + '/subcategories')[0]
    types = call('GET', '/api/admin/catalog/subcategories/' + str(subcategory['id']) + '/request-types', token=admin)
    fixture = next((t for t in types if t['code'] == 'QA_G1_EXTERNAL_ANONYMOUS'), None)
    if fixture is None:
        fixture = call('POST', '/api/admin/catalog/request-types', {
            'subcategoryId': subcategory['id'], 'code': 'QA_G1_EXTERNAL_ANONYMOUS', 'name': 'Caso descartable G1',
            'description': 'Datos sintéticos del laboratorio QA', 'ticketType': 'COMPLAINT', 'responsibleAreaId': 'M3',
            'minimumPriority': 'LOW', 'baseRisk': 'LOW', 'affectedPopulationFactor': 0,
            'allowsAnonymous': True, 'requiresLocation': False}, admin, 201)
    require(fixture['allowsAnonymous'] and fixture['responsibleAreaId'] == 'M3', 'EXPECTED_G1_FIXTURE_REQUIRED')
    call('PUT', '/api/admin/catalog/request-types/' + str(fixture['id']) + '/form', {'fields': [
        {'code': 'qaSynthetic', 'label': '¿Es una prueba sintética?', 'type': 'BOOLEAN', 'required': True,
         'allowUnknown': False, 'displayOrder': 1, 'config': {}, 'riskRules': []}]}, admin)
    neighborhood = call('GET', '/api/catalog/neighborhoods')[0]['id']
    def queues():
        output = command(DC + ['exec', '-T', 'rabbitmq', 'rabbitmqctl', '-q', 'list_queues', '-p', 'core',
                               'name', 'messages_ready', 'messages_unacknowledged'], timeout=30)
        rows = [row.split() for row in output.splitlines()]
        return {row[0]: row[1:] for row in rows if len(row) == 3
                and row[0] in ('q.qa.ingress', 'q.atencion-ciudadana', 'q.qa.rejected')}
    original_queues = queues()
    require(set(original_queues) == {'q.qa.ingress', 'q.atencion-ciudadana', 'q.qa.rejected'}, 'QA_TOPOLOGY_REQUIRED')
    run = str(uuid.uuid4())
    ids = []
    start = time.monotonic()
    broker = containers['rabbitmq']['Id']
    stopped = False
    try:
        stopped = True  # finally restores even if docker stop times out after stopping the broker.
        command(['docker', 'stop', '--time', '10', broker], timeout=30)
        require(not json.loads(command(['docker', 'inspect', broker]))[0]['State']['Running'], 'BROKER_NOT_STOPPED')
        outage_start = time.monotonic()
        print('1/5 RabbitMQ QA detenido; Backend y PostgreSQL siguen activos.', flush=True)
        for index in range(args.tickets):
            created = call('POST', '/api/tickets', {'requestTypeId': fixture['id'],
                'summary': 'INT04 ' + run + ' ' + str(index), 'description': 'Bache de prueba local sin peligro inmediato',
                'formData': {'qaSynthetic': True}, 'anonymousAccessPassword': str(uuid.uuid4()),
                'location': {'addressLine': 'Dirección sintética QA', 'neighborhoodId': neighborhood,
                             'latitude': -34.6037, 'longitude': -58.3816}}, expected_status=201)
            ticket = str(uuid.UUID(created['ticketId']))
            ids.append(ticket)
            call('POST', '/api/tickets/' + ticket + '/review', {}, agent)
            require(call('POST', '/api/tickets/' + ticket + '/route', {}, agent)['currentStatus'] == 'ROUTED', 'ROUTE_FAILED')
        selection = ','.join("'" + ticket + "'::uuid" for ticket in ids)
        def outbox():
            return sql('SELECT coalesce(json_agg(x ORDER BY x.event_id),\'[]\') FROM (SELECT event_id,ticket_id,event_type,'
                       'update_type,status,retry_count,last_failure_code,auto_publish_eligible,payload FROM outbox_events '
                       'WHERE ticket_id IN (' + selection + ')) x;')
        def tickets():
            return sql('SELECT json_agg(x ORDER BY x.id) FROM (SELECT id,current_status,core_origin_verified,citizen_id '
                       'FROM tickets WHERE id IN (' + selection + ')) x;')
        pending = outbox()
        require(len(pending) == args.tickets and all(e['update_type'] == 'ROUTED' for e in pending),
                'EXPECTED_ONE_ANONYMOUS_ROUTED_EVENT_PER_TICKET')
        require(all(e['status'] == 'PENDING' and e['auto_publish_eligible'] for e in pending), 'OUTBOX_NOT_PENDING')
        require(len(tickets()) == args.tickets and all(t['current_status'] == 'ROUTED' and not t['core_origin_verified']
                and t['citizen_id'] is None for t in tickets()), 'PERSISTENCE_OR_QA_IDENTITY_FAILED')
        print(f'2/5 Persistidos {len(ids)} tickets y {len(pending)} eventos PENDING; eventId conservados. '
              f'Caída mínima: {args.outage_seconds}s.', flush=True)
        while time.monotonic() - outage_start < args.outage_seconds:
            time.sleep(1)
        # A failed connection is committed only after its timeout; very short outages
        # must wait for that durable evidence rather than race the worker transaction.
        wait_for(lambda: any(e['retry_count'] > 0 and e['last_failure_code'] == 'BROKER_UNAVAILABLE' for e in outbox()),
                 30, 'NO_REAL_OUTBOX_RETRY_OBSERVED')
        pending_after = outbox()
        require(all(e['status'] == 'PENDING' for e in pending_after), 'OUTAGE_EXHAUSTED_DURABLE_EVENTS')
        max_retry = max(e['retry_count'] for e in pending_after)
        command(['docker', 'start', broker])
        stopped = False
        outage_elapsed = time.monotonic() - outage_start
        print('RabbitMQ QA reiniciado; esperando reconexión y backoff de Outbox, sin reiniciar Backend.', flush=True)
        wait_for(lambda: all(e['status'] == 'PUBLISHED' for e in outbox()), args.timeout_seconds, 'OUTBOX_RECOVERY_TIMEOUT')
        published = outbox()
        require({e['event_id']: e['payload'] for e in pending} == {e['event_id']: e['payload'] for e in published},
                'ORIGINAL_EVENT_OR_PAYLOAD_CHANGED')
        def receipts():
            return {e['eventId']: e for e in call('GET', '/qa/events')['events']}
        expected_ids = {e['event_id'] for e in pending}
        wait_for(lambda: expected_ids <= receipts().keys(), args.timeout_seconds, 'CORE_RECEIPT_TIMEOUT')
        print(f'3/5 Recuperación automática: {len(published)} publicados y recibidos por Core; máximo {max_retry} reintentos durante caída.', flush=True)
        incoming = []
        for ticket in ids:
            sent = next(e for e in published if str(e['ticket_id']) == ticket and e['update_type'] == 'ROUTED')['payload']
            now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
            event = {'eventId': str(uuid.uuid4()), 'eventType': 'updateTicketStatus', 'eventVersion': '1.0',
                     'occurredAt': now, 'sourceModule': 'obras', 'correlationId': sent['correlationId'],
                     'causationId': sent['eventId'], 'data': {'ticketId': ticket, 'updateType': 'STARTED',
                     'publicMessage': 'Prueba sintética INT04 iniciada', 'updateOccurredAt': now,
                     'updatedBy': {'type': 'SYSTEM', 'id': None}}}
            call('POST', '/qa/publish', event, expected_status=202)
            incoming.append(event)
        incoming_selection = ','.join("'" + e['eventId'] + "'::uuid" for e in incoming)
        def effects():
            return sql('SELECT json_build_object(\'inbox\',(SELECT count(*) FROM inbox_events WHERE event_id IN ('
                       + incoming_selection + ') AND status=\'PROCESSED\'),\'activities\',(SELECT count(*) FROM ticket_activities '
                       'WHERE external_event_id IN (' + incoming_selection + ')),\'messages\',(SELECT count(*) FROM ticket_messages '
                       'WHERE ticket_id IN (' + selection + ') AND text=\'Prueba sintética INT04 iniciada\'));')
        wait_for(lambda: effects()['inbox'] == args.tickets, args.timeout_seconds, 'INBOX_RECOVERY_TIMEOUT')
        before = effects()
        require(before == {'inbox': args.tickets, 'activities': args.tickets, 'messages': args.tickets}, 'INITIAL_EFFECTS_MISMATCH')
        replay = [{'direction': 'M2_TO_CORE', 'event': e['payload']} for e in published]
        replay += [{'direction': 'CORE_TO_M2', 'event': e} for e in incoming]
        command(DC + ['exec', '-T', 'core-mock', 'java', '--class-path', '/core/BOOT-INF/lib/*',
                      '/core/CoreMock.java', 'replay-int04'], json.dumps(replay), timeout=60)
        wait_for(lambda: all(receipts()[key].get('deliveries', 0) >= 3 for key in expected_ids),
                 args.timeout_seconds, 'CORE_DUPLICATE_DELIVERY_TIMEOUT')
        def drained():
            relevant = {key: value for key, value in queues().items() if key != 'q.qa.rejected'}
            return len(relevant) == 2 and all(counts == ['0', '0'] for counts in relevant.values())
        wait_for(drained, args.timeout_seconds, 'REPLAY_DRAIN_TIMEOUT')
        require(queues()['q.qa.rejected'] == original_queues['q.qa.rejected'], 'REPLAY_REJECTED_INSTEAD_OF_DEDUPLICATED')
        require(effects() == before and all(t['current_status'] == 'IN_PROGRESS' for t in tickets()), 'DUPLICATE_BUSINESS_EFFECT')
        require(all(receipts()[key]['effects'] == 1 for key in expected_ids), 'DUPLICATE_CORE_EFFECT')
        for service in ('backend', 'postgres', 'core-mock'):
            current = json.loads(command(['docker', 'inspect', containers[service]['Id']]))[0]
            require(current['State']['StartedAt'] == containers[service]['State']['StartedAt'] and current['State']['Running'],
                    'UNEXPECTED_SERVICE_RESTART')
        require(call('GET', '/health')['status'] == 'UP', 'CORE_NOT_HEALTHY')
        print('4/5 Dos reenvíos físicos por eventId en ambos sentidos: un único efecto en Core e Inbox/actividad/mensaje.', flush=True)
        print(json.dumps({'status': 'PASS', 'runId': run, 'tickets': args.tickets, 'outboxPublished': len(published),
                          'originalEventIds': sorted(expected_ids), 'inboxProcessed': len(incoming),
                          'physicalReplayCopies': len(replay) * 2, 'businessEffectsPerIncomingEvent': 1,
                          'maxRetriesDuringOutage': max_retry, 'brokerOutageSeconds': round(outage_elapsed, 2),
                          'backendPostgresCoreRestarted': False, 'seconds': round(time.monotonic() - start, 2)}), flush=True)
        print('5/5 INT-04 PASS. Datos sintéticos conservados; otra ejecución usa nuevos UUID y verifica sólo su lote.', flush=True)
    finally:
        if stopped:
            command(['docker', 'start', broker])
            print('RabbitMQ QA restaurado después de una interrupción de la demo.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as failed:
        # No HTTP bodies, SQL, passwords, tokens or Docker inspect content in diagnostics.
        code = str(failed) if isinstance(failed, RuntimeError) else type(failed).__name__
        print('INT-04 FAIL: ' + code, flush=True)
        raise SystemExit(1)

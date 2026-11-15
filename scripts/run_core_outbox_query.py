#!/usr/bin/env python3
"""Query runner: validated metadata goes to Summary; raw SSH output never goes to logs."""
import csv
import io
import os
import sys

from core_outbox_query import FIELDS, LIMIT, lookup, validate_report
from core_outbox_approval import require
from run_core_outbox_approval import ssh_request


def summary(report, selection):
    lines = [f"Consulta por `{selection['lookup_by']}` = `{selection['lookup_id']}`", '',
             f"Resultado: **{report['code']}**. Eventos: **{len(report['rows'])}**.", '',
             'Orden: created_at descendente, event_id descendente. Fechas created_at/published_at en UTC; '
             'published_at_argentina es hora local America/Argentina/Buenos_Aires, sin offset. '
             '`NULL` representa un valor ausente. Los estados son una observación de PostgreSQL; '
             'esta consulta no publica ni solicita confirmaciones nuevas al broker.', '']
    if report['code'] not in ('OK', 'NO_RESULTS'):
        return '\n'.join(lines + ['No se obtuvieron datos verificables. No se modificaron registros ni aprobaciones.']) + '\n'
    if not report['rows']:
        return '\n'.join(lines + ['Sin eventos asociados al UUID consultado.']) + '\n'
    if report['truncated']:
        lines += [f'**Resultado parcial: sólo los {LIMIT} eventos más recientes. Hay más eventos; '
                  'esta salida no representa el historial completo.**', '']
    values = [[str(row[key]) if row[key] is not None else 'NULL' for key in FIELDS] for row in report['rows']]
    lines += ['| ' + ' | '.join(FIELDS) + ' |', '| ' + ' | '.join('---' for _ in FIELDS) + ' |']
    lines.extend('| ' + ' | '.join(row) + ' |' for row in values)
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(FIELDS)
    writer.writerows(values)
    lines += ['', '<details><summary>CSV para copiar y compartir con Core</summary>', '',
              '```csv', output.getvalue().rstrip('\n'), '```', '', '</details>']
    return '\n'.join(lines) + '\n'


def main():
    try:
        selection = lookup(os.environ.get('OUTBOX_LOOKUP_BY', ''), os.environ.get('OUTBOX_LOOKUP_ID', ''))
    except Exception:
        print('::error::Invalid lookup: select ticket_id or event_id and enter one canonical UUID (event_id requires v4).')
        return 1
    try:
        result = ssh_request(selection, os.environ, command='query-core-outbox-v1', timeout=730)
        report = validate_report(result.stdout, selection)
        success = report['code'] in ('OK', 'NO_RESULTS')
        require((result.returncode == 0) == success)
        rendered = summary(report, selection)
        print(f"Outbox query: {report['code']}; rows={len(report['rows'])}; truncated={str(report['truncated']).lower()}. See Summary.")
        exit_code = 0 if success else 1
    except Exception:
        rendered = 'Resultado: **UNVERIFIED**. No se pudo verificar una respuesta segura. Revisar configuración/SSH/scripts; no se muestran detalles remotos.\n'
        exit_code = 1
    if exit_code:
        print('::error::Outbox query failed; no raw remote output was logged. See Summary.')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(rendered)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())

"""Summarize one private local Agent trace; no networking, writes or price guesses."""
import argparse
import json
import os
import re
import stat

from .agent_cli import safe_outcome
from .agent_runner import REJECTION_STAGES, FINISH_REASONS
from .deepseek import MODEL, usage_counts
from .search_http import strict_json


MAX_BYTES = 262144
TOOLS = {'search_documents', 'read_document_version', 'create_archive_plan',
         'execute_archive_plan', 'save_handover_report'}
REASONS = {'success', 'cancelled', 'timeout', 'provider_invalid', 'provider_auth',
           'provider_balance', 'provider_rate_limit', 'provider_unavailable',
           'provider_rejected', 'model_invalid', 'dependency_failed', 'budget_exhausted'}
STATUSES = {'success', 'waiting_confirmation', 'processing', 'unavailable', 'invalid_request',
            'access_denied', 'conflict', 'busy', 'timeout', 'dependency_failed', 'cancelled'}


def integer(value, maximum, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('invalid audit')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('invalid audit')
    return value


def read_trace(path):
    # NONBLOCK avoids hanging on FIFOs before fstat can reject non-regular files.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_size > MAX_BYTES):
            raise ValueError('invalid audit')
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('invalid audit')
    truncated = bool(raw and not raw.endswith(b'\n'))
    lines = raw.split(b'\n')
    if any(len(line) > 4096 for line in lines) or len(lines) > 129:
        raise ValueError('invalid audit')
    # The writer appends newline per record. An incomplete final write is not a result.
    records = [strict_json(line) for line in lines[:-1]]
    if not records:
        raise ValueError('invalid audit')
    return records, truncated


def summarize(path):
    """Counts are trace observations, not invoices or authoritative business state."""
    try:
        records, truncated = read_trace(path)
        first = records[0]
        if not isinstance(first, dict) or first.get('event') not in {'task_start', 'archive_resume'}:
            raise ValueError('invalid audit')
        task = identifier(first.get('task_id'))
        models, tools, request_ids = {}, {}, set()
        outcome = None
        rejection = None
        for index, event in enumerate(records):
            if (not isinstance(event, dict) or event.get('task_id') != task or outcome is not None):
                raise ValueError('invalid audit')
            integer(event.get('time_unix_ms'), 10**15)
            kind = event.get('event')
            if index == 0:
                continue
            if kind == 'task_outcome':
                outcome = safe_outcome(event)
                if rejection is not None and outcome['state'] not in {'failed','unknown','cancelled','interrupted'}:
                    raise ValueError('invalid audit')
                continue
            if rejection is not None: raise ValueError('invalid audit')
            if kind == 'model_rejection':
                fields={'sequence','stage','finish_reason','content_kind','tool_calls_kind','tool_call_count'}
                if set(event)!=fields|{'event','task_id','time_unix_ms'}: raise ValueError('invalid audit')
                sequence=integer(event.get('sequence'),8,1)
                if sequence!=len(models) or models[sequence]['result'] is None: raise ValueError('invalid audit')
                for key,allowed in (('stage',REJECTION_STAGES),('finish_reason',FINISH_REASONS),
                                    ('content_kind',{'absent','null','string','other'}),
                                    ('tool_calls_kind',{'absent','null','list','other'})):
                    if not isinstance(event[key],str) or event[key] not in allowed: raise ValueError('invalid audit')
                if event['tool_calls_kind']=='list': integer(event['tool_call_count'],5)
                elif event['tool_call_count'] is not None: raise ValueError('invalid audit')
                rejection={key:event[key] for key in fields}
                continue
            if kind not in {'model_attempt', 'model_result', 'tool_attempt', 'tool_result'}:
                raise ValueError('invalid audit')
            is_model = kind.startswith('model_')
            entries = models if is_model else tools
            sequence = integer(event.get('sequence'), 128, 1)
            if is_model:
                if first['event'] == 'archive_resume' or event.get('model') != MODEL:
                    raise ValueError('invalid audit')
                identity = identifier(event.get('request_id'))
            else:
                identity = event.get('tool')
                if identity not in TOOLS:
                    raise ValueError('invalid audit')
            if kind.endswith('_attempt'):
                if sequence != len(entries) + 1:
                    raise ValueError('invalid audit')
                if is_model:
                    if identity in request_ids: raise ValueError('invalid audit')
                    request_ids.add(identity)
                    integer(event.get('request_bytes'), 65536, 1)
                    integer(event.get('max_tokens'), 8192, 1)
                entries[sequence] = {'identity': identity, 'result': None}
            else:
                entry = entries.get(sequence)
                if not entry or entry['identity'] != identity or entry['result'] is not None:
                    raise ValueError('invalid audit')
                integer(event.get('duration_ms'), 86400000)
                if event.get('reason' if is_model else 'status') not in (REASONS if is_model else STATUSES):
                    raise ValueError('invalid audit')
                entry['result'] = event

        def counts(entries):
            results = [e['result'] for e in entries if e['result'] is not None]
            return {'attempts': len(entries), 'results': len(results), 'unresolved': len(entries) - len(results),
                    'duration_ms_observed': sum(e['duration_ms'] for e in results)}

        model_counts = counts(list(models.values()))
        tool_counts = {name: counts([e for e in tools.values() if e['identity'] == name])
                       for name in sorted(TOOLS) if any(e['identity'] == name for e in tools.values())}
        complete = not truncated and outcome is not None and all(e['result'] is not None
                            for e in list(models.values()) + list(tools.values()))
        known = [usage_counts(e['result'].get('usage')) for e in models.values() if e['result'] is not None]
        known = [value for value in known if value is not None]
        keys = ['prompt_tokens', 'completion_tokens', 'total_tokens']
        # A missing cache breakdown is unknown, not a cache miss or zero-priced input.
        if known and all('prompt_cache_hit_tokens' in value for value in known):
            keys += ['prompt_cache_hit_tokens', 'prompt_cache_miss_tokens']
        observed = {key: sum(value[key] for value in known) for key in keys}
        unknown = len(models) - len(known)
        return {'schema_version': 1, 'task_id': task, 'trace_complete': complete,
            'truncated_tail': truncated, 'outcome': outcome,
            'model': {**model_counts, 'usage_known_calls': len(known), 'usage_unknown_calls': unknown,
                      'usage_observed': observed, 'usage_total': observed if complete and not unknown else None},
            'tools': tool_counts, 'model_rejection': rejection,
            'cost_estimate': None, 'business_acceptance': 'not_evaluated'}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise ValueError('audit unavailable or invalid') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', help='one owned regular 0600 JSONL file in a protected directory')
    args = parser.parse_args()
    try:
        result = summarize(args.trace)
    except ValueError:
        print(json.dumps({'error': 'audit_unavailable_or_invalid'}))
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == '__main__': raise SystemExit(main())

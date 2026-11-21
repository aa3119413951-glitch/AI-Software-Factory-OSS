#!/usr/bin/env python3
"""Read-only, single-shot continuity readiness advice; no provider polling."""
from __future__ import annotations

import argparse
import json

EVENTS = ('manual', 'session-switch', 'execution-ending', 'major-decision')


def assess(used_tokens: int | None = None, limit_tokens: int | None = None,
           event: str | None = None, checkpoint: float = 0.70,
           prepare: float = 0.85, stop: float = 0.90) -> dict:
    if event is not None and event not in EVENTS:
        raise ValueError('unsupported event')
    if not (0 < checkpoint < prepare < stop <= 1):
        raise ValueError('invalid thresholds')
    measured = used_tokens is not None or limit_tokens is not None
    if measured:
        if (used_tokens is None or limit_tokens is None or
                type(used_tokens) is not int or type(limit_tokens) is not int or
                limit_tokens <= 0 or not 0 <= used_tokens <= limit_tokens):
            raise ValueError('provide a valid, complete pair of measured token counts')
        fraction = used_tokens / limit_tokens
    else:
        fraction = None
    if event is not None or (fraction is not None and fraction >= prepare):
        state = 'HANDOVER_PREPARING'
    elif fraction is not None and fraction >= checkpoint:
        state = 'CHECKPOINT_DUE'
    elif fraction is not None:
        state = 'WORKING'
    else:
        state = 'UNOBSERVABLE'
    return {
        'state': state,
        'usage_fraction': round(fraction, 4) if fraction is not None else None,
        'avoid_new_long_tasks': bool(fraction is not None and fraction >= stop),
        'event': event,
        'actions': (
            ['refresh desk/CURRENT.md and pending; export available chat branch with Human consent']
            if state == 'HANDOVER_PREPARING' else
            ['refresh checkpoint and evidence pointers'] if state == 'CHECKPOINT_DUE'
            else []
        ),
        'warning': 'Only measured values supplied by the caller are assessed; no automatic token access or export.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--used-tokens', type=int)
    parser.add_argument('--limit-tokens', type=int)
    parser.add_argument('--event', choices=EVENTS)
    args = parser.parse_args()
    try:
        print(json.dumps(assess(args.used_tokens, args.limit_tokens, args.event), ensure_ascii=False, indent=2))
        return 0
    except ValueError as exc:
        parser.error(str(exc))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())

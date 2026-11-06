# Task inspection boundary: offline characterization

Base: `910af8c2182363f9c1ef10109810b257c0f73866`; before implementing explicit constraints.

`probe.py` used the frozen H05 question and synthetic fixtures against the real
C++ HTTP server with isolated SQL. It is characterization, not acceptance:

- Frozen question preserved; metadata page 1 returned 10 items, page 2 returned 1.
- Body read and keyword-content search succeeded despite the question's limits.
- Other task owner's request returned 404; proposed structured constraints returned 400.
- No reports, archive decisions, model requests or credential reads.

`probe.log` records one passing characterization test (5.971 seconds); unit
`smartdocs-inspection-boundary-20261106.service` finished successfully with exit 0.
`preflight.txt` and `postflight.txt` match all 173 prior runtime/source/fixture
fingerprints. Afterward the bounded slice had zero tasks, MemAvailable 1523896 KiB,
and memory.events high 82471, max/oom/oom_kill 0 (unchanged).

Conclusion: existing authorization does not interpret natural-language task limits.
The user approved a new explicit immutable constraint, enforced by C++, rather than
an inference from prose. New constraint tests must not replace or relabel old H05
results. The product increment is verified separately; this probe records old behavior.

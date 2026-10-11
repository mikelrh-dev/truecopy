# Delta for answering

## ADDED Requirements

### Requirement: Logged answer provenance

When an answer is logged to the operator query log, the record SHALL carry the
corpus revision that produced it (`r{N}`). The revision SHALL be read from the
store once per operator question; a call with no operator log MUST NOT read the
revision (eval and harness traffic pay nothing). A record whose revision cannot
be resolved (no corpus) MAY carry the empty string, preserving the historical
shape.

#### Scenario: Logged answer stamps its revision

- GIVEN an operator log and a current corpus revision rN
- WHEN a question is answered and logged
- THEN the record carries `revision="rN"`

#### Scenario: Cached hit stamps the same revision

- GIVEN a cache hit for the current revision
- WHEN the answer is logged
- THEN its record carries the same `revision` as the miss path would

#### Scenario: Harness traffic reads no revision

- GIVEN an answering call with no operator log
- WHEN it answers
- THEN no revision is read from the store

## MODIFIED Requirements

### Requirement: Opt-in calibration recording in eval

The eval entry point SHALL accept an additive `--record` flag. Without
`--record`, eval SHALL write NOTHING: no calibration file is created, read, or
appended. With `--record`, eval SHALL append exactly one calibration record when
the run passes AND the sweep produced a point, and MUST NOT mutate
`config.MIN_SCORE`. When `--record` is given and no record is appended, the
reason (failing checks, an empty sweep, or a disabled path) SHALL be explained
on stderr; stdout MUST stay byte-identical to the run without the flag. In both
modes, checks, printed lines, and exit codes SHALL remain unchanged, including
the report-only line stating that eval did not change `config.MIN_SCORE`.

#### Scenario: No flag, no writes

- GIVEN a configured calibration store path
- WHEN eval runs without `--record`
- THEN no calibration file is created or appended
- AND output, checks, and exit code match pre-change behavior

#### Scenario: Record appends exactly one line

- GIVEN eval completes successfully with `--record`
- WHEN the record is written
- THEN exactly one record line is appended
- AND `config.MIN_SCORE` is unchanged
- AND the report-only line still prints

#### Scenario: Record requested but nothing written

- GIVEN `--record` and a run that cannot produce a record
- WHEN eval finishes
- THEN the reason is printed on stderr
- AND stdout is byte-identical to the same run without the flag
- AND the exit code is unchanged

#### Scenario: Disabled store with record flag

- GIVEN `CALIBRATION_PATH=""`
- WHEN eval runs with `--record`
- THEN nothing is written and checks, lines, and exit codes are unchanged

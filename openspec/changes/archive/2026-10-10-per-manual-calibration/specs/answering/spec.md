# Delta for answering

## ADDED Requirements

### Requirement: Opt-in calibration recording in eval

The eval entry point SHALL accept an additive `--record` flag. Without `--record`, eval SHALL write NOTHING: no calibration file is created, read, or appended. With `--record`, eval SHALL append exactly one calibration record and MUST NOT mutate `config.MIN_SCORE`. In both modes, checks, printed lines, and exit codes SHALL remain unchanged, including the report-only line stating that eval did not change `config.MIN_SCORE`.

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

#### Scenario: Structured golden counts

- GIVEN the golden checks have run
- WHEN the record is appended
- THEN it carries golden_pass and golden_total as structured fields

#### Scenario: Disabled store with record flag

- GIVEN `CALIBRATION_PATH=""`
- WHEN eval runs with `--record`
- THEN nothing is written and checks, lines, and exit codes are unchanged

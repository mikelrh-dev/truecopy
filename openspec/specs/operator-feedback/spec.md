# operator-feedback Specification

## Purpose

Let operators rate answers with thumbs from the web UI and aggregate votes into a deterministic weekly triage report, without corrupting existing query logs or skewing answer stats.

## Assumptions

- **A1** — The widget appears only where a rateable last answer exists (answer or refusal kinds). Scheduler timeout notices are NOT rateable; there is nothing to rate.
- **A2** — Aggregation defaults to the last 7 days (`--days 7`) and the top 10 topics (`--top 10`), both overridable by report flags.
- **A3** — No voter identity is recorded (log convention: facts about answers, never operator identity).
- **A4** — Topic keys are deterministic local keyword reductions (no LLM); the report is a triage aid, not a metric of record.

## Requirements

### Requirement: Thumbs rating on rateable answers

After an answer or refusal is shown in the web UI, the system SHALL offer a thumbs-up/thumbs-down control, shown only while a rateable last answer exists and no vote has been cast for it. A vote MUST be recorded exactly once per shown answer; asking a new question resets that state. Recording a vote is best-effort: a failed write MUST NOT block the operator.

#### Scenario: Rate a good answer

- GIVEN an answer (kind answer) is displayed and unvoted
- WHEN the operator clicks thumbs-up
- THEN one thumbs-up vote is recorded
- AND the control confirms the vote and is disabled

#### Scenario: Rate a refusal

- GIVEN a refusal is displayed
- WHEN the operator clicks thumbs-down
- THEN one thumbs-down vote is recorded against that question and kind

#### Scenario: Timeout is not rateable

- GIVEN only a scheduler timeout notice exists and no answer was produced
- WHEN the question page renders
- THEN no thumbs control is offered

#### Scenario: One vote per shown answer

- GIVEN a vote was already cast for the currently shown answer
- WHEN the same answer remains displayed
- THEN no further vote can be recorded for it

#### Scenario: New question resets the widget

- GIVEN an answer was rated
- WHEN the operator submits a new question
- THEN the next rateable answer offers the control again

### Requirement: Feedback records never corrupt answer history

Votes SHALL be appended as feedback event records in the same query log, under the same write lock. Reading answer records MUST keep working on both pre-change logs and mixed old/feedback logs. Feedback records MUST NOT count toward answer stats.

#### Scenario: Pre-change logs still read

- GIVEN a queries.jsonl containing only pre-change answer lines
- WHEN answer records are read
- THEN every valid pre-change line is returned unchanged

#### Scenario: Mixed log reads both kinds

- GIVEN a log with pre-change lines, new answer lines, and feedback event lines
- WHEN answer records are read
- THEN answer lines of both generations are returned and feedback lines are excluded

#### Scenario: Feedback is separately readable

- GIVEN a log containing feedback event lines
- WHEN feedback records are read
- THEN each vote is returned with its question, kind, and vote value

#### Scenario: Stats ignore votes

- GIVEN a log containing answer lines and feedback event lines
- WHEN stats are computed
- THEN the counts reflect answer records only

### Requirement: Deterministic weekly report

A report command SHALL aggregate feedback from the query log over a configurable window (default 7 days) and list the top topics by negative sentiment (default top 10), with optional JSON output. It MUST use only deterministic local computation (no LLM, no new services) and MUST fail loudly when the log is unreadable. The hermetic suite MUST cover it.

#### Scenario: Default window and cap

- GIVEN a log with feedback from the last 7 days
- WHEN the report runs without flags
- THEN it covers the last 7 days and lists at most 10 topics

#### Scenario: Flag override

- GIVEN feedback spread over 30 days
- WHEN the report runs with an explicit window override
- THEN the aggregation window follows the flags

#### Scenario: Unreadable log fails loudly

- GIVEN the log path is missing or unreadable
- WHEN the report runs
- THEN it exits with a clear error instead of a silent empty report

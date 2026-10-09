# Proposal: Local-Only Plant RAG System

## Intent

Spanish-speaking operators consult 200–1200-page English machine manuals with no tool. Deliver a complete local-only RAG assistant: Spanish answers citing Manual+Page, never inventing, safety-aware, on the owner's 16GB/8GB PC, LAN-only.

## Scope

### In Scope
- Phase 1: text-only Siemens 828D (Haas images deferred)
- Answer contract: citation, exact not-found fallback, safety warnings
- FIFO queue, single worker; rule-based safety priority + aging; 45s timeout with position
- LAN web UI (mobile/tablet); login: operator code + password
- Single current manual, full reindex, `revision` metadata from day one
- Owner self-operation (upload, reindex, codes)
- Spanish queries/answers; cross-lingual ES→EN retrieval
- Full system, not an MVP

### Out of Scope
- Haas images / `[IMAGEN]` handling
- External APIs, cloud, any documentation egress
- SSO, fine-grained roles, multi-admin backoffice
- Historical manual versions; LLM safety classifier

## Capabilities

### New Capabilities
- `manual-ingestion`: batched ingestion, parent-child chunks, `revision` metadata, reindex, corpus growth
- `retrieval`: cross-lingual ES→EN search over current manual (text + alarm tables)
- `answering`: Spanish contract — citation, not-found fallback, safety warnings
- `query-queue`: FIFO worker, safety priority (closed terms), aging, 45s timeout + position
- `web-access-auth`: LAN browser, code+password login, no app

### Modified Capabilities
None.

## Approach

Candidate stack (`Caso de uso.txt`, not decided): PyMuPDF batch ingestion, parent-child chunks, BGE-M3 CPU, Qdrant mmap, safety rule triage, Ollama Qwen2.5 7B Q4, web UI (Streamlit); **to be validated in sdd-design** within the 16GB/8GB budget.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/ingest`, `src/retrieve`, `src/serve` | New | ingestion, answering, queue + web |
| `openspec/specs/<capability>/spec.md` | New | five capability specs |
| Local service config (Qdrant, Ollama) | New | offline, LAN-only |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Exceeds 16GB/8GB budget | Med | Batching, mmap; spike measured in design |
| Hallucinated/unsafe answer | Med | Three mandatory contract tests |
| 45s timeout breached | Med | Queue-position UX; pilot metrics |
| Stack candidate unfit | Med | Validate in sdd-design before tasks |

## Rollback Plan

Greenfield: remove change code and change folder; Qdrant data disposable, rebuilt by re-ingestion. Nothing leaves the LAN.

## Dependencies

- Runtime: Python, Docker (Qdrant), Ollama — no internet
- Corpus: `documentation/828D_DA_1015_en_en-US.pdf`
- Not a git repo yet; apply inits version control

## Success Criteria

- [ ] Spanish answers with Manual+Page citation within 45s
- [ ] Query outside corpus → exact "No aparece en la documentación"
- [ ] Safety query preempts queue; aging prevents starvation
- [ ] Three tests pass: alarm retrieval, ES→EN, refusal
- [ ] Within 16GB RAM/8GB VRAM; zero external API calls

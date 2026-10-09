# Design: Local-Only Plant RAG System

## Technical Approach

Greenfield Python 3.11 package `src/plant_rag`, one module per spec: batched ingestion, cross-lingual retrieval, contract answering, priority queue, LAN web. Qdrant and Ollama local; no egress. Bounded: 16GB RAM / 8GB VRAM.

## Architecture Decisions

### Decision 1: Reindex strategy — A3 corrected (ADR-001)

| Option | Tradeoff | Decision |
|---|---|---|
| Offline pause (A3 as written) | Simple; 20–25min outage; unclear state after failure | ✗ |
| In-place wipe & rebuild | Live queries see mixed corpora — breaks manual-ingestion "never mix" | ✗ |
| Blue/green: build `manual_r{N+1}_{text,alarms}`, swap `current_*` aliases in one call, delete old | Orchestration code; RAM Δ≈0 (vectors ≈15MB; embedding dominates either way) | ✓ no pause; interrupted build leaves `r{N}` serving; per-query `revision` filter forbids mixed responses |

### Decision 2: Stack validated vs budget (ADR-002)

Pins: pymupdf 1.24.14, sentence-transformers 3.4, torch 2.5.1-cpu, `BAAI/bge-m3` fp32, qdrant v1.12 + client 1.12, Ollama ≥0.5 + `qwen2.5:7b-instruct` (Q4_K_M, 4.68GB), streamlit 1.41, argon2-cffi.

| Line | RAM GB | VRAM GB |
|---|---|---|
| OS + Streamlit + queue + app | 3.0 | — |
| Ollama Qwen 7B resident + KV@8k | 0.3 | 5.6 |
| Qdrant Docker/WSL2 + mmap cache | 1.5 | — |
| BGE-M3 fp32 on CPU | 2.5 | 0 |
| **Serving total** | **7.3** | **5.6** |
| + ingestion peak (100pp batches) | ≈9–11 | 5.6 |

Fits 16/8. Replacements: camelot → `find_tables()` (no ghostscript/opencv); BGE-M3 GPU → CPU (VRAM collision); Qwen 14B, Chroma, Unstructured rejected. Kept: mmap, batching, Streamlit, Docker Qdrant (native fallback if WSL2 is larger).

### Decision 3: Alarm exact path (A2) (ADR-003)

Dedicated `..._alarms`, payload-indexed on `alarm_code`; rows from `find_tables()` first cell matching `^\d{4,6}$` (line-regex fallback). Code-shaped queries use exact `MatchValue` on `current_alarms`, never embeddings; unknown → refusal.

### Decision 4: Answer contract enforcement (ADR-004)

Empty retrieval or unknown alarm returns exact `No aparece en la documentación` **without an LLM call**. Else one Ollama call (Spanish prompt, English parents, `num_ctx=8192`, `temperature=0.2`, `num_predict≤600`); every `[Fuente: … Página N]` validated against retrieved pages, violation ⇒ refusal. Safety match ⇒ Spanish warning. LLM output untrusted.

### Decision 5: Queue scheduling (ADR-005)

Single worker; heap key `(aged, safety_rank, seq)` with `aged = wait≥15s`, `safety_rank=0` on closed terms: aged beats fresh safety (anti-starvation), safety beats fresh routine, `seq` keeps FIFO. Deadline `enqueued_at+45s` gates dispatch and generation ⇒ Spanish timeout (pos. ES), never partial.

### Decision 6: LAN web + auth (ADR-006)

Bind Streamlit to LAN interface (+firewall rule). Argon2 hashes in `users.json`; owner flag gates upload/reindex/codes. Uploads `.pdf`-only, UUID-renamed, parsed in-process.

## Data Flow

    Operator─ES pregunta ─▶ Queue(+45s) ─▶ posición N; Worker: heap(aged,safety,seq)
    Worker─Qdrant(current alias + revision filter): code-shaped? exact filter
                                  └ no ─▶ BGE-M3(CPU) ─▶ top-k children
    empty ─▶ exact refusal (no LLM); else Ollama(parents+question) ─▶ ES answer

    Owner─upload+reindex ─▶ r{N+1}: 100pp ─▶ pymupdf ─▶ chunk ─▶ BGE(CPU)
    ─▶ Qdrant upsert ─▶ validate ─▶ one alias swap ─▶ delete r{N}
    failure ⇒ aliases untouched, r{N} serves; UI estado ES

## File Changes

| File | Action | Description |
|---|---|---|
| `src/plant_rag/ingest/{loader,chunker,alarms,reindex}.py` | Create | batches, parent-child, tables, swap |
| `src/plant_rag/retrieve/{embedder,store,fastpath}.py` | Create | BGE-M3 CPU, alias+revision, alarm filter |
| `src/plant_rag/answer/{prompt,llm,citations}.py` | Create | Ollama, refusal gate, citations |
| `src/plant_rag/scheduling/scheduler.py` | Create | heap order, deadline, position |
| `src/plant_rag/web/{app,auth,ui_es}.py` | Create | Streamlit, login, owner ops |
| `scripts/*.py`, `docker-compose.yml`, `requirements.txt`, `users.json.example` | Create | self-operation, Qdrant, pins, credentials |

## Interfaces / Contracts

```python
@dataclass(frozen=True)
class Hit: manual_id: str; page: int; revision: str; child: str; parent: str

@dataclass(frozen=True)
class Answer:
    kind: Literal["answer", "refusal", "timeout"]
    text_es: str
    citations: tuple[Citation, ...]  # (manual_id, page)

REFUSAL_ES = "No aparece en la documentación"
# Qdrant: on_disk collections; both aliases swapped in one call
```

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | heap order, regex, citation validator, refusal gate | pytest pure functions |
| Integration | no mixed revisions; batch RSS bound | local Qdrant + RSS asserts |
| E2E | 3 mandatory checks, 45s timeout, login | scripted eval (A4) |

## Threat Matrix

| Boundary | Applicability |
|---|---|
| Doc-like paths | N/A — PDF-only, UUID-renamed, in-process |
| Git repository selection | N/A — no VCS automation |
| Commit state | N/A — no commits |
| Push state | N/A — no push |
| PR commands | N/A — no PR automation |

## Migration / Rollout

No migration: greenfield; ingestion creates revision 1; Qdrant disposable; rollback per proposal.

## Open Questions

- [ ] Qdrant alias atomicity — RED swap test; revision filter forbids mixing
- [ ] `find_tables()` recall on 828D alarms (eval #1 gates it)
- [ ] Docker RAM and p50 latency vs <5s (A1)

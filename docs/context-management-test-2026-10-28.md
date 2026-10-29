# Context-management functional test — 2026-10-28

**Question:** when the context gets large, does Ekalavya shrink it without losing the core
explanation? Two mechanisms shrink context:

- **Grouped curation.** When the collected material is longer than the one-pass limit
  (`one_pass_kchars` in research settings, 80,000 characters by default), the curator
  splits it into groups. Each group is read in parallel and turned into notes, the notes
  are checked against that group's text, and then they are merged into one lesson plan.
  Detail that doesn't fit becomes references the writers can look up later.
- **Chat compaction.** When older turns reach 3,000 characters, they are summarised.
  The last `chat_turns_kept` (7 by default) user turns stay word for word.

**Result: PASS.** Both plans kept all 10 core items, and the compacted chat kept 5 of 5
facts. The run used free models only: 17 calls, $0. No video was rendered.

## How to rerun

Script: [scripts/qa/qa_context_management.py](../scripts/qa/qa_context_management.py).
It uses whatever models the API container is set up with. For a $0 run, start the
free-model override first (see [docker-compose.qa-free.yml](../docker-compose.qa-free.yml)).
"Attention Is All You Need" must already be uploaded to some chat; the script finds it
by title, or you can pass `--chat <id>`.

```bash
# Git Bash: set MSYS_NO_PATHCONV=1 first
docker compose cp scripts/qa/qa_context_management.py api:/tmp/qa_context.py
docker compose exec -w /tmp api python /tmp/qa_context.py              # everything (~15 min on free models)
docker compose exec -w /tmp api python /tmp/qa_context.py --step memory  # compaction only (~1 min)
docker compose exec -w /tmp api python /tmp/qa_context.py --step curate  # the two plans only
```

To test source code that isn't in the image yet, copy it in and run against it:
`docker compose cp src/. api:/tmp/src` and add `-e PYTHONPATH=/tmp/src` to `exec`.

The script exits 0 on a pass and 1 on a fail. The plans, summary and scores are saved
in the container under `/tmp/.local/qa/context/`. The script never writes to the
database: compaction runs on an in-memory SQLite copy.

**Pass bar:** every core pattern below appears in each plan, and every compaction check
holds. The checks are regex and keyword matches, so they are only a floor. Read the
printed plans too, to judge depth and whether they fit the lesson length.

## Setup of the 2026-10-28 run

| | |
|---|---|
| Material | "Attention Is All You Need": 167 passages, 39,430 characters |
| Request | "Explain the main idea of the paper 'Attention Is All You Need' in a 3 minute lesson." |
| Curator / helper model | `nvidia/nemotron-3.5-super-vl:free` (fallback `dots-studio/dots-3-note-preview:free`) |
| One-pass run | limit 80,000 characters, so the whole paper is read in one prompt |
| Grouped run | limit 10,000 characters, which forces 4 groups that are read in parallel and merged |
| Code | Commit f2e192d plus the uncommitted curator and teacher-voice prompt fixes |

## Curation: does the core survive?

| Core item (pattern) | One pass | Grouped |
|---|---|---|
| Transformer: no recurrence or convolution | ✅ | ✅ |
| Self-attention | ✅ | ✅ |
| Scaled dot-product attention | ✅ | ✅ |
| `softmax(QKᵀ/√d_k)V`, exact | ✅ | ✅, plus the variance reason for √d_k |
| Queries, keys and values | ✅ | ✅ |
| Multi-head attention (h=8, d_k=64) | ✅ | ✅, plus the equation and matrix shapes |
| Positional encoding | ✅ (as a reference only) | ✅ with the sin/cos formulas |
| Encoder and decoder stacks (N=6) | ✅ | ✅ |
| BLEU 28.4 / 41.8 | ✅ | ✅ |
| Parallelism and training cost | ✅ | ✅, with the complexity comparison |
| **Total** | **10/10** | **10/10** |

**One-pass plan** (6 sections):
1. The problem with recurrent and convolutional models.
2. The Transformer proposal, in the paper's own words.
3. The scaled dot-product attention equation.
4. Multi-head attention and the encoder and decoder stacks.
5. Results.
6. Closing.

These went to references: positional encoding, the feed-forward sub-layer, training
details, constituency parsing, ablations and attention visualisations.

**Grouped plan** (6 sections):
1. The recurrence bottleneck, with the complexity table.
2. Scaled dot-product attention and why it divides by √d_k.
3. Multi-head attention, with its equation and shapes.
4. The stack, the feed-forward network and the positional-encoding formulas.
5. Results, ablations and parsing F1.
6. The one idea to take away.

These went to references: the optimizer schedule, hardware and training time, label
smoothing, inference settings, the full results table and attention visualisations.

### Findings

- **Grouping did not lose the core.** It kept more detail than the one-pass plan, because
  each group marks its central notes with "(core)" and the merge keeps them.
- **The one-pass plan trims harder.** It moved positional encoding to a reference, so the
  writer can look it up but won't see it by default.
- **The grouped plan is too dense for 3 minutes.** Section 5 alone has the full results
  table, ablations and parsing scores, so the script writer has to cut a lot. If this
  shows up in real runs, have the merge step size the plan to the lesson length (in the
  prompt, not as a fixed rule).

## Chat compaction: do early preferences survive?

The test chat has 28 messages:
- 4 user turns set preferences and get answers: first-person teacher voice; Tamil
  subtitles with English narration; video 1 on F = ma with a 1200 kg car; the learner's
  confusion about mass versus acceleration; "only my NCERT chapter, no web".
- Then 20 filler messages follow.

Compaction keeps the last 7 user turns word for word and summarises the rest.

| Kept in the summary | Result |
|---|---|
| First-person teacher voice | ✅ |
| Tamil text with English voice | ✅ |
| Only the NCERT chapter, no web | ✅ |
| Video 1 and its numbers (1200 kg, 2 m/s², 2400 N) | ✅ |
| The mass-versus-acceleration confusion | ✅ |
| **Total** | **5/5** |

The summary is organised into four parts: preferences, what was asked and answered,
videos made, and open confusion. It dropped the filler. A rerun of `--step memory` on the
same day also passed 5/5.

## Prompt changes this test checked

- **Curator role:** keep the core even in a short lesson. Name the central concept, law
  or result, and give the one equation, number or relation that makes it precise.
  References are for genuine extras, never for the core. The ending must not tease or
  promise later content.
- **Group extraction:** mark central notes with "(core)" so the merge keeps them.
- **Coverage check:** look first for the central concept and what makes it precise.

## Limits

- The test uses one paper. It shows that grouping can keep the core, not that it always
  will on every source. For a different source, add a new `CORE` pattern set to the script.
- Free models are weaker than the production models, so a pass here is a lower bound.
  A failure on free models may not reproduce on paid ones.

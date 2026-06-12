# GigTrack — Report Structure

Outlines for the two written deliverables, budgeted to the page limits. Follow
the official LMS templates where they differ; these are content guides. Cover
page is unnecessary (counts as a page if included). Use prose with a smooth
logic flow — avoid screenshot dumps (graded under Writing, 15%).

---

## A. Proposal & Progress Report — **4 pages** (due Mon 22 Jun, 10%)

Goal: show the team is on track with a reasonable plan, and briefly describe how
both relational and non-relational databases are used.

| Section | Content | ~Pages |
|---|---|---|
| 1. Application & motivation | What GigTrack is, the problem it solves, target users (customers + admins), why it needs a polyglot stack. | 0.5 |
| 2. Data & datasets | Source/shape of data (synthetic catalogue + plan for a real dataset); entities and volumes. | 0.5 |
| 3. Database design (relational + NoSQL) | ER diagram (figure); the 8 MySQL tables and key relationships; the 3 MongoDB collections and *why* they're documents; one line on Redis + object storage. | 1.5 |
| 4. Execution plan & progress | What's done vs to-do, with a simple timeline/Gantt; risks + mitigations. Show steady pacing (not back-loaded). | 1.0 |
| 5. Roles & responsibilities | Who owns what (6 members). | 0.5 |

Figures to include: ER diagram (`docs/er_diagram.mermaid`), maybe the
architecture diagram (`docs/data_flow.svg`).

---

## B. Final Report — **8 pages** (due Mon 13 Jul)

Must contain sections for **both** relational and non-relational components.
Reuse parts of the progress report. The Database aspect is 40% and Application
20% of the grade, so weight sections 4–6 accordingly.

| Section | Content | ~Pages |
|---|---|---|
| 1. Introduction | Application, objectives, target users, the value proposition. Reuse from proposal, tightened. | 0.5 |
| 2. Data & datasets | Final datasets, volumes, how data was generated/imported, any cleaning. | 0.5 |
| 3. Database design | ER diagram + translation to relations; normalisation notes; the NoSQL data model and justification; the SQL↔NoSQL boundary (logical FKs). Cite `docs/schema_design.md`. | 1.5 |
| 4. Relational implementation | CRUD coverage; constraints (FK, CHECK); **triggers** (seat inventory + VIP pricing); **advanced queries** (nested/correlated, window function, CTE, transaction); indexing. Short query snippets, not full dumps. | 1.5 |
| 5. NoSQL implementation | Collections, indexes (incl. text index); CRUD via PyMongo; **aggregation** (`$facet`, `$lookup`); object storage for media (pointer-vs-blob pattern). | 1.0 |
| 6. Application & system | Architecture (figure), request flow across the 4 stores, caching strategy, key features (booking quota, admin dashboard), and **security** (parameterised SQL, bcrypt, CSRF, cookies). | 1.0 |
| 7. Performance evaluation | Benchmark methodology + results across the six groups (cache vs uncached, indexed vs scan, per-store point-lookup access time, server-side compute, write latency incl. trigger overhead, commit batching, concurrent throughput); percentiles + CPU/memory; the chart; brief analysis. Mention the correctness suite (`tests/`, pytest) one line — performance and correctness are tested separately. | 0.75 |
| 8. GenAI reflection | How GenAI tools were used, pros/cons, and how to use them better next time. | 0.5 |
| 9. Conclusion & future work | What was achieved; next steps (real dataset, payments, mobile). | 0.25 |
| — Appendix (optional) | Non-critical extras, extra screenshots (≤10 advanced-feature screenshots for the source-code submission). | (extra) |

Figures: ER diagram, architecture/data-flow diagram, benchmark chart, 1–2 UI
screenshots. Keep figures captioned and referenced from the text.

---

## Grading reminder (from the brief)

- Proposal & Progress (10%) · Presentation (15%) · **Database (40%)** ·
  Application (20%) · Writing (15%).
- Database is the largest slice — make sections 3–5 the strongest: clear ER,
  justified NoSQL modelling, real constraints/triggers, advanced queries, and
  security. Tie every claim to something in the codebase.

# Retrieval Policy

Context Graph should not fetch all notes for every request.

It should build a compact context pack with this order:

1. Extract query signals:
   `type`, `project`, `domain`, `flow`, `goal`, `severity`, `status`
2. Fetch exact marker matches first.
3. Add one-hop explicit relations from those matches.
4. Add inferred relations only when the request is exploratory, architectural, or ambiguous.
5. Re-rank by:
   - exactness
   - relation distance
   - severity
   - freshness
   - status relevance
6. Return only the smallest set that explains the current task.

## Ranking guidance

- exact scope beats inferred scope
- confirmed relations beat probable relations
- critical and in-progress records beat done records
- rules and decisions decay slower than tasks and incidents

## Output shape

Each context pack should contain:

- direct matches
- supporting relations
- promoted rules
- unresolved risks
- omitted-but-nearby records count

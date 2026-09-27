# AI v4 replay READ return type repair

Migration `0050_business_v4_replay_read_cast` repairs the 0047 replay READ
function. The receipt table stores `candidate_digest` as `varchar(64)`, while
the function declares that result column as `text`. PostgreSQL `RETURN QUERY`
requires an explicit cast for this return shape. The replacement adds only
`p.candidate_digest::text` to the projection.

Before replacement, the migration checks the exact 0047 READ body, its result
signature, SECURITY DEFINER settings, owner, NOLOGIN role, function and table
privileges, PUBLIC EXECUTE revocation, and direct seal denial. `CREATE OR
REPLACE` retains the function identity and ACL. The claim checks before and
after the SELECT and the 0048 replay writer are unchanged.

Reverse migration restores the frozen 0047 body only when the replay receipt
table is empty. Once any candidate has been recorded, reverse migration fails
closed because restoring the old body would make that receipt unreadable. The
target PostgreSQL test records real promotion and finance candidates using the
sealer role, reads them under the matching claim, rejects a wrong claim, and covers
function body drift and both reverse-migration boundaries. The isolated
PostgreSQL target run passed four tests, including the single-segment sealer
core integration, at `.runtime/ai-pg-a1a7fdb52846/tests.log`. Production
upgrade and rollback rehearsal remain separate gates. The isolated 0049→0050
rehearsal passed at
`.runtime/ai-pg-7ddc07cd8f88/business-v4-replay-read-cast-upgrade-evidence.json`:
79 old AI tables and renderer 1–7 bytes are preserved, only the READ body
changes while its OID/ACL/signature remain fixed, before/after archives restore,
and an empty receipt ledger can reverse and reapply the migration. No
production migration or role activation was performed.

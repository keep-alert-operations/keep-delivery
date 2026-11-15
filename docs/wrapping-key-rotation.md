# Wrapping-key overlap and bounded rewrap

The application accepts supplied AES-256 wrapping versions. It generates only
per-record data keys/nonces; it never generates platform wrapping secrets,
changes customer API keys, or removes old wrapping versions. Existing v1
envelopes remain readable when their key IDs remain supplied.

Mount a restricted keyring JSON document through `KEEP_WRAPPING_KEYRING`.
The schema is `{"current":"version-id","keys":{"version-id":"base64"},
"purposes":{"session-csrf":"csrf-version","cursor":"cursor-version"}}`.
Every referenced ID must exist in `keys`, with standard base64 encoding of
exactly 32 bytes. The displayed schema contains placeholders, not usable secrets.
`purposes` is optional for legacy compatibility; omitted purposes use `current`.
Supported purposes are `operator-key`, `session-csrf`, `receipt`, `cursor`,
`search-cursor`, and `rewrap-cursor`; REV-73 removed the provider-token and
login-transaction purposes. Supply separate
cursor/CSRF versions as required by system completion §21. Purpose and record
identity remain authenticated even when reading a legacy shared wrapping key.

The optional purpose map and key-ID grammar (1–128 ASCII letters, digits,
underscore, dot or hyphen, starting with a letter/digit) are assistant-selected
configuration details. Duplicate JSON fields, unknown purposes, missing keys,
malformed envelopes and authentication failures are rejected. The legacy single
`KEEP_WRAPPING_KEY`/`KEEP_WRAPPING_KEY_ID` settings remain supported by the
settings adapter; select either that source or a mounted keyring.

Deploy readers with old and new versions before switching all writers to new
active IDs. Run under an approved workload database role with access to only the
required tables. The existing worker role is exercised locally; deployment
permissions and secret-store/recovery guarantees still require supplied evidence.

```sh
python -m keep.shared.rewrap operator-credentials --verify-only --limit 100
python -m keep.shared.rewrap operator-credentials --limit 100
python -m keep.shared.rewrap operator-credentials --cursor '<returned next_cursor>'
```

Run each family: `operator-credentials`, `session-csrf` and `ingest-receipts`.
Each invocation processes at most
100 records, yields after approximately 100 ms between records, and bounds each
CAS statement to 100 ms with a 50 ms lock wait. The initial keyset read uses the
database's ordinary bounded read timeout. Resume while `next_cursor` is non-null.
The encrypted operational continuation binds its family and target wrapping ID;
it is not a browser cursor and cannot authorize a database operation by itself.
Changing the family's target requires restarting its scan.

Rewrap authenticates both ciphertext layers under the original exact AAD, then
rewraps the same data key. Payload ciphertext and payload nonce are unchanged.
Database writes compare the exact old ciphertext and its AAD identity columns.
A concurrent write, another rewrap, or ciphertext erasure wins that comparison;
the operation reports a conflict instead of overwriting or resurrecting material.
No lifecycle, verifier, token identity, retry fingerprint or expiry is modified.
Output contains only family/key IDs, counts, and encrypted continuation. Failure
output contains no exception details or secret-bearing driver parameters.

After all writers switch, repeat full verification passes until all stored
families show zero `needs_rewrap` and resolve every conflict/error. A scan alone
does not authorize removing a wrapping version: concurrent inserts can sort
behind a keyset cursor, outstanding browser/search cursors are not stored here,
and supported backups/restores may still need older keys. Preserve overlap for
at least the longest valid client cursor (currently 15 minutes), and preserve
the recovery versions
required by the platform policy. The tool neither retires keys nor schedules
their removal. The selected rotation schedule remains 90 days and compromise.

Local verification uses actual AES-GCM and isolated PostgreSQL: purpose/AAD
rejection, legacy overlap, unchanged ciphertext payload, fixed cursor expiry,
all three record families, restricted worker role, bounded pages, parallel rewrap,
and revocation and newer-ciphertext races. No company secret store or remote system is touched;
these checks do not qualify production key custody or recovery.

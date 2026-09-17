# Live native batch compatibility

Status: initial probe failed; reviewed correction passed the live repeat. No deployment.

On2026-09-17 at19:29UTC, the candidate adapter at `784f7e3` was exercised against
two fixed public objects, using the already-authorized local GitHub account.
[V1 output](../../experiments/growth/results/live-native-batch-v1.json) preserves
the failure: one867-byte response, decoder returned no batch, and the probe's
assertion failed. The second object was not attempted. No token or raw content
was retained in the report. The Python wrapper printed a failure report but exited
zero; interpret `status`, not that wrapper's exit code, as the experiment verdict.

A separate fixed read showed all three directory entries in the root response
had `size: 0`. The decoder required null; the independent synthetic fixture also
emitted null. Current [GitHub TreeEntry schema](https://docs.github.com/en/graphql/reference/git#treeentry)
declares size as non-null integer. Thus the parser and fixture shared an incorrect
assumption. The earlier field probe reconstructed hashes without validating this
field, so it did not catch the mismatch either.

The correction accepts exactly integer zero for nonblob entries, rejects
null/boolean/negative/nonzero malformed metadata, and retains the internal unknown-
size marker for nonblobs. Blob sizes and reconstructed typed hashes still receive
their existing checks. This is not permission to trust GitHub size metadata as
cryptographic evidence: tree hashes do not encode child sizes.

Two focused regressions first failed on the old decoder and fixture, then passed
after correction. The worker's focused module passed19 tests; root independently
ran both batch modules with23 tests passing in1.31 seconds. A one-import synthetic
smoke retained canonical/proof parity with zero fixture contract violations.
These checks do not substitute for rerunning the actual public-object probe.

The [live repeat](../../experiments/growth/results/live-native-batch-v2.json) at
19:36 UTC on `ed3a58b7a98aabf078a2fc5dff378a61720ab864` passed both object/hash
checks: two requests, 2,217 response bytes, two reported GraphQL points. The tree
reconstructed to216 bytes and the text blob to1,097 bytes. Total probe time was
1.33 seconds. These two requests establish this narrow real-response compatibility,
not production capacity or an Actions-token quota guarantee. The failed first
probe remains unchanged.

## Reproduce the adapter check

Use the report's source revision and the project Python environment. The credential
is captured in memory, not placed on a command line or printed. This makes two
bounded read-only requests to the fixed public deployment; no repository writes.

```python
import hashlib
import subprocess
from omarchy_knowledge.github_native import GitHubRead

token = subprocess.check_output(
    ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL).strip()
adapter = GitHubRead(read_token=token)
del token
for kind, oid in [
    ("tree", "4e2b946d27f177bd6a8150d09bfe224beeead49e"),
    ("blob", "22a6812881dcdf71872d7b7ef05bb53a8cfa4a90"),
]:
    decoded = adapter._read_object_batch(kind, [oid])
    assert decoded is not None
    raw = decoded[oid].raw
    actual = hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode()
                          + b"\0" + raw).hexdigest()
    assert actual == oid
print({"calls": adapter.http.calls,
       "response_bytes": adapter.http.bytes,
       "graphql_points": adapter.http.graphql_points})
```

This snippet reproduces the checks, not the report wrapper's metadata formatting.
A later successful tiny probe is still not a live500-record capacity measurement,
an Actions-token quota measurement, or a deployment approval. The fixture's earlier
successful comparison remains tied to its exact measured source and assumptions.

## Whole current public catalog

A separate [read-only cold canonical check](../../experiments/growth/results/live-public-cold-v1.json)
on the same code completed at19:37 UTC:26 records,26 authenticated receipts,
15 total requests (nine GraphQL points),146,249 response bytes and8.68 seconds
including local proof replay. It used no previous proof seed. The exported proof
was24,208 bytes and replayed identically. This exercises the real current catalog,
not500 public records or the write/admission path. Upstream metadata was not refreshed.

To reproduce, construct `GitHubRead` with the in-memory token as above, then:

```python
import subprocess
from omarchy_knowledge.coordinator import Policy
from omarchy_knowledge.canonical import read_canonical
from omarchy_knowledge.service import _validated_proof

revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
policy = Policy(revision, revision)
data = read_canonical(adapter, policy)
proof = _validated_proof(adapter, policy, data)
print({"records": len(data["records"]), "receipts": len(data["receipts"]),
       "proof_bytes": len(proof), "calls": adapter.http.calls})
```

Use a fresh adapter, not one already used for the two-object check. The catalog can
grow, so later results may legitimately differ. The recorded revision identifies
the exact tested source. No installed client, production pin or data was changed.

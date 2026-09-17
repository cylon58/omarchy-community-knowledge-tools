# GraphQL field compatibility probe

Date: 2026-09-17. Read-only probe, not a capacity test or deployed optimization.
[Recorded summary](../../experiments/growth/results/graphql-field-probe-v1.json).

Two authenticated public GitHub requests were made using an existing account:
first discover the public repository's main/root identity, then fetch two fixed
objects in one request. No credentials, raw blob content or private data were
retained. This is separate from the zero-network synthetic growth runs.

The discovery returned main `3139acf1d07453d99e3ae72e00f96153eb23e060` and
root tree `4e2b946d27f177bd6a8150d09bfe224beeead49e`. The tested blob was the
public MIT license, `22a6812881dcdf71872d7b7ef05bb53a8cfa4a90`.

## Reproduce the request

Use this fixed query with `gh api graphql -f query='…'` and an existing authorized
read session; never paste a token into the query or result notes:

```graphql
query {
  repository(owner: "cylon58", name: "omarchy-community-knowledge") {
    databaseId
    treeObject: object(oid: "4e2b946d27f177bd6a8150d09bfe224beeead49e") {
      __typename
      oid
      ... on Tree { entries { nameRaw mode type oid size } }
    }
    blobObject: object(oid: "22a6812881dcdf71872d7b7ef05bb53a8cfa4a90") {
      __typename
      oid
      ... on Blob { byteSize isBinary isTruncated text }
    }
  }
  rateLimit { cost remaining }
}
```

## Reproduce the checks

The original command piped that JSON through Python 3.14.7. This is the byte
reconstruction used, with `value` holding the decoded response:

```python
import base64
import hashlib

assert "errors" not in value
repo = value["data"]["repository"]
tree, blob = repo["treeObject"], repo["blobObject"]
rows = [(base64.b64decode(e["nameRaw"], validate=True), e)
        for e in tree["entries"]]
raw = b"".join(
    format(e["mode"], "o").encode() + b" " + name + b"\0" + bytes.fromhex(e["oid"])
    for name, e in sorted(rows, key=lambda p: p[0] +
                         (b"/" if p[1]["type"] == "tree" else b"")))
content = blob["text"].encode("utf-8")
def git_oid(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode()
                        + b"\0" + data).hexdigest()
assert git_oid("tree", raw) == tree["oid"]
assert git_oid("blob", content) == blob["oid"]
assert len(content) == blob["byteSize"]
```

The tree's six entries used decimal modes 16384 and 33188 (Git octal 40000 and
100644). Both reconstructed hashes and the blob size matched; the blob was
nonbinary and untruncated. The batch reported query cost 1. Remaining account
quota is a historical observation, not a reproducible constant or an Actions
token measurement. Other modes, encodings, partial errors and large trees remain
untested by this tiny probe; production code would need stricter parsing and all
the bounds in the [candidate design](../plans/cold-recovery-options.md).

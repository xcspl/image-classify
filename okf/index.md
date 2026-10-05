---
okf_version: "0.2"
house_version: "0.3"
bundle: okf
handling:
  - Read this index before opening any doc in this bundle.
  - Navigate by index entries; never glob the tree.
  - A write to any directory updates that directory's index.md in the same commit.
  - Full rules live in okf-guide.md in the workspace registry (Playbook at its bundle root).
---

# Decision

* [Design: how image-classify classifies a library](design.md) - Why two wire formats in one client, OKF and JSON as outputs of one cache, content-hash caching, a fixed category list plus merged tags, Asset docs per image, and a deterministic generated bundle.

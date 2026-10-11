# Calculator dependencies

These are selected source snapshots, not separate applications or generated builds.

- `ndvideo`: H264BSD and Xvid decoders, ARM pixel conversion, and the small platform/clock helpers reused from ND Video Player. Its source manifest lists the Xvid decoder files.
- `nsdl`: the 49 source files used by nSDL's TI-Nspire build, their local headers, and original build/license material. Desktop SDL ports and Autoconf infrastructure are omitted.

Selections were checked against successful ARM compiler dependency reports. A receiver rebuilt before and after trimming produced identical bytes with the same compiler, build inputs and source timestamp. Removing unrelated platforms does not change calculator support or runtime performance.

Each directory's `provenance.json` records its origin, local changes and file hashes. `python tools/ci_support.py verify` checks these inputs. Additions or modifications need corresponding provenance updates. Build output belongs in `build/`, not here.

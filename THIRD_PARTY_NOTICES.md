# Third-party notices

## Three.js

`webapp/static/vendor/three.module.js` and `OrbitControls.js` are bundled from Three.js under the MIT
License. The upstream notice is retained in
[`webapp/static/vendor/THREE-LICENSE.txt`](webapp/static/vendor/THREE-LICENSE.txt).

## Dependencies and services

Python dependencies installed from `requirements.txt` retain their own licenses. Unity and AI tools
are separate installations. Their product names and trademarks belong to their respective owners.

## Downloaded assets

Poly Haven and Sketchfab assets are downloaded during user runs, not bundled here. They retain their
original licenses. LudoFabric records attribution in `Assets/ThirdParty/CREDITS.md`; verify source
licenses and applicable attribution/share-alike requirements before redistribution.

## Python runtime dependencies (installed via pip, not bundled)

- LangGraph and langgraph-checkpoint-sqlite: MIT License (LangChain, Inc.). Their dependencies include the LangChain core and LangSmith SDK packages; LudoFabric does not require a LangSmith account or configure hosted tracing.
- Pillow: MIT-CMU License.

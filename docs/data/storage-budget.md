# Local storage budget

## Gate

Stage 00 requires 30～50GB of free space on the project drive. On 2026-09-21, E had approximately 47.6GB free and passed this gate.

## Initial allocation

| Category | Working budget |
|---|---:|
| Raw compressed and extracted data | 12GB |
| Interim and processed Parquet | 10GB |
| Graph artifacts and sampled datasets | 8GB |
| Python environment and package cache | 5GB |
| Model artifacts and MLflow runs | 5GB |
| Docker images and volumes | 5GB |
| Safety margin | 2GB |

Total initial budget: approximately 47GB.

## Controls

- Keep `.uv-cache` removable and separate from source files;
- Avoid copying the complete wide transaction table in memory or on disk more than necessary;
- Prefer Parquet and column projection for processed data;
- Keep raw, interim and processed artifacts outside Git;
- Review disk use before adding Docker images, local LLM weights or Neo4j volumes;
- Do not delete unrelated user files automatically to recover capacity.

If free space falls below 10GB, pause large downloads or model pulls and revise this budget before continuing.

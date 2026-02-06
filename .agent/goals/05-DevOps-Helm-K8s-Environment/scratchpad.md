# Goal 05: DevOps Tooling - Helm/K8s Development Environment

> **Status**: 🟡 In Progress
> **Priority**: P1 (High) — Enables deployment work
> **Created**: 2026-02-07
> **Updated**: 2026-02-07 00:30

## Overview

Add Azure CLI, Helm, kubectl, and related Kubernetes tooling to `flake.nix` to enable development and testing of a Helm chart for deploying legal-mcp server to Kubernetes (targeting Azure AKS).

Chart and values live in `.devops/helm/` — separated from application code.

## Success Criteria

- [x] `flake.nix` includes Azure CLI (`azure-cli`)
- [x] `flake.nix` includes Helm 3 (`kubernetes-helm`)
- [x] `flake.nix` includes kubectl (`kubectl`)
- [x] `flake.nix` includes k9s for cluster TUI (`k9s`)
- [x] ~~`flake.nix` includes kind or minikube for local testing~~ — Removed, deploying directly to AKS
- [x] Dev shell prints quick reference for K8s commands
- [x] Basic Helm chart structure created (`.devops/helm/legal-mcp/`)
- [x] CD pipeline (`cd.yml`) wired to deploy Helm chart to AKS
- [ ] Chart deployed to AKS testing cluster (needs Azure secrets configured)
- [x] Documentation for Azure AKS deployment workflow
</newtml>

<old_text line=62>
| Task-01 | Add K8s tools to flake.nix | 🟢 | 30 min | - |
| Task-02 | Create Helm chart skeleton | 🟢 | 1 hour | Task-01 |
| Task-03 | Implement Deployment + Service + Ingress + HPA + PVC | 🟢 | 1 hour | Task-02 |
| Task-04 | Create values files (default, example, aks-testing) | 🟢 | 30 min | Task-03 |
| Task-05 | AKS testing deployment | ⚪ | 1 hour | Task-04 |
| Task-06 | Document deployment workflow | 🟢 | 30 min | Task-04 |

## Context & Background

**Why this goal?**

German consulates need to deploy legal-mcp in cloud environments (Azure) to provide AI-assisted legal analysis for building compliance. A Helm chart enables:

1. **Reproducible deployments** — Same chart for dev, staging, prod
2. **Configuration management** — Values files per environment
3. **Scaling** — Horizontal pod autoscaling for high load
4. **Azure integration** — AKS-specific features (managed identity, Key Vault)

**Current state:** No Kubernetes tooling in `flake.nix`, no Helm chart exists.

## Constraints & Requirements

### Hard Requirements
- Tools must work within the existing FHS environment
- No breaking changes to current dev workflow
- Helm chart must follow best practices (Chart.yaml v2, values.yaml)

### Soft Requirements
- Prefer Azure-native solutions where applicable
- Chart should support both stdio and SSE transport modes
- Support for GPU nodes (for embedding models)

### Out of Scope
- Actual Azure infrastructure provisioning (Terraform/Bicep)
- CI/CD pipeline setup (separate goal)
- Production secrets management (separate concern)

## Approach

**Phase 1:** Add K8s tooling to flake.nix (30 min) ✅
**Phase 2:** Create basic Helm chart structure (1-2 hours) ✅
**Phase 3:** AKS testing deployment (1 hour)
**Phase 4:** Documentation (30 min) ✅

## Tasks

| Task ID | Description | Status | Estimate | Depends On |
|---------|-------------|--------|----------|------------|
| Task-01 | Add K8s tools to flake.nix | 🟢 | 30 min | - |
| Task-02 | Create Helm chart skeleton | 🟢 | 1 hour | Task-01 |
| Task-03 | Implement Deployment + Service + Ingress + HPA + PVC | 🟢 | 1 hour | Task-02 |
| Task-04 | Create values files (default, example, aks-testing) | 🟢 | 30 min | Task-03 |
| Task-05 | Wire CD pipeline + AKS deployment | 🟡 | 1 hour | Task-04 |
| Task-06 | Document deployment workflow | 🟢 | 30 min | Task-04 |

**Total estimate:** ~4-5 hours

## Task Details (High-Level)

### Task-01: Add K8s Tools to flake.nix ✅

**Files modified:**
- `flake.nix` — Added `azure-cli`, `kubectl`, `kubernetes-helm`, `k9s`, `netcat-openbsd`
- Added K8s/Helm and Azure quick reference to runScript
- Decided against `kind` — deploying directly to AKS, no local cluster needed

### Task-02: Create Helm Chart Skeleton ✅

**Created structure:**
```
.devops/helm/
├── legal-mcp/              # The chart
│   ├── Chart.yaml          # v0.0.1, appVersion 0.0.1
│   ├── values.yaml         # Default values
│   ├── README.md           # Full deployment docs
│   └── templates/
│       ├── _helpers.tpl    # Name/label/image helpers
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── serviceaccount.yaml
│       ├── ingress.yaml
│       ├── hpa.yaml
│       ├── pvc.yaml
│       └── NOTES.txt       # Post-install instructions
└── values/                 # Environment-specific overrides
    ├── example.yaml        # Minimal quick-start
    └── aks-testing.yaml    # AKS testing with debug, persistence, ingress
```

### Task-03: Implement Deployment + Service + Ingress + HPA + PVC ✅

**Deployment features:**
- Container image from GHCR (configurable to ACR)
- Non-root user (UID 1000), no privilege escalation, drop all caps
- Configurable liveness/readiness probes on `/health`
- `--transport sse --port 8000` args from values
- Environment variables via `env` and `envFrom` (secrets/configmaps)
- Optional PVC volume mount at `/app/data`

**Service:** ClusterIP default, configurable type
**Ingress:** NGINX class, TLS-ready, disabled by default
**HPA:** CPU/memory scaling, disabled by default
**PVC:** Optional persistent storage, Azure `managed-csi` in AKS values

### Task-04: Create Values Files ✅

**Default** (`values.yaml`): Production-ready defaults, persistence off, ingress off
**Example** (`values/example.yaml`): Minimal config for quick deployment
**AKS Testing** (`values/aks-testing.yaml`):
- `image.pullPolicy: Always`, tag `latest`
- `legalMcp.logLevel: DEBUG`, `debug: true`
- Persistence on with `managed-csi` storage class (5Gi)
- Ingress on with NGINX class
- Lenient probes (60s initial delay)
- Prometheus scrape annotations

**Validation:** `helm lint` passes, `helm template` renders correctly for both values files.

### Task-05: Wire CD Pipeline + AKS Deployment 🟡

**Done:**
- Replaced `deploy-placeholder` job in `.github/workflows/cd.yml` with real Helm-based AKS deployment
- Pipeline flow: `check-release` → `helm-lint` → `deploy`
- `helm-lint` validates chart + template rendering before touching the cluster
- `deploy` job: Azure Login → AKS credentials → namespace create → `helm upgrade --install --atomic`
- Deployment verifies rollout status and prints pod info
- Environment-to-values-file mapping: staging → `aks-testing.yaml`, production → TBD
- Kept Docker Compose and Fly.io as commented-out alternatives
- Image repo (`ghcr.io/l4b4r4b4b4/legal-mcp`) matches existing `release.yml` output

**Remaining (needs repo secrets):**
- [ ] Configure `AZURE_CREDENTIALS` secret (service principal JSON)
- [ ] Configure `AKS_RESOURCE_GROUP` secret
- [ ] Configure `AKS_CLUSTER_NAME` secret
- [ ] Create GitHub environments (`staging`, `production`) with protection rules
- [ ] First actual deploy and smoke test

**Manual workflow:**
1. `az login && az aks get-credentials --resource-group <rg> --name <cluster>`
2. `helm upgrade --install legal-mcp .devops/helm/legal-mcp -f .devops/helm/values/aks-testing.yaml -n legal-mcp --create-namespace`
3. `kubectl -n legal-mcp get pods`
4. `kubectl -n legal-mcp port-forward svc/legal-mcp 8000:8000`

### Task-06: Document Deployment Workflow ✅

**Created:** `.devops/helm/legal-mcp/README.md` with:
- Quick start, AKS deployment, ACR integration
- Full configuration reference (all values documented)
- Troubleshooting guide (logs, connectivity, common issues)
- Development section (template rendering, linting)

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| FHS env compatibility issues | Medium | Low | Test each tool individually |
| Azure CLI auth complexity | Low | Medium | Document `az login` workflow |
| GPU scheduling in kind | Medium | Medium | Use real AKS for GPU testing |

## Dependencies

- **Upstream**: None (independent goal)
- **Downstream**: 
  - Any future CI/CD work
  - Production deployment planning

## Notes & Decisions

### Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-07 | Helm 3 only (no Tiller) | Modern standard, simpler security model |
| 2026-02-07 | Skip kind, deploy to AKS directly | No need for local cluster simulation |
| 2026-02-07 | Chart in `.devops/helm/` not `charts/` | Keep devops separate from app code |
| 2026-02-07 | Separate values files per environment | Clean separation, easy to diff |
| 2026-02-07 | Non-root UID 1000, drop all caps | Security best practices from the start |
| 2026-02-07 | `helm upgrade --install --atomic` in CD | Auto-rollback on failed deploy |
| 2026-02-07 | Helm lint step before deploy | Catch chart errors before touching cluster |
| 2026-02-07 | Staging maps to aks-testing.yaml | Reuse existing values, prod values TBD |

### Open Questions

- [x] ~~Dockerfile needed before Task-05 can proceed~~ — Already exists at `docker/Dockerfile`, `docker/Dockerfile.base`
- [ ] Should chart support both SQLite (dev) and PostgreSQL (prod)?
- [ ] GPU node pool configuration for embedding inference?
- [ ] Azure Key Vault integration via CSI driver?
- [x] ~~CI/CD pipeline for automated helm deploy on push?~~ — Done, `cd.yml` wired to Helm
- [ ] Create production values file (`values/aks-production.yaml`)

## References

- [Helm Best Practices](https://helm.sh/docs/chart_best_practices/)
- [Azure AKS Documentation](https://docs.microsoft.com/en-us/azure/aks/)
- [kind Quick Start](https://kind.sigs.k8s.io/docs/user/quick-start/)
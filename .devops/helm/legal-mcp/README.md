# Legal-MCP Helm Chart

A Helm chart for deploying Legal-MCP - a comprehensive legal research MCP server providing AI assistants with structured access to legal information across multiple jurisdictions.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- (Optional) Azure AKS cluster for production deployment

## Installation

### Quick Start

```bash
# From the repository root
helm install legal-mcp .devops/helm/legal-mcp

# With custom values
helm install legal-mcp .devops/helm/legal-mcp -f .devops/helm/values/example.yaml
```

### AKS Testing Deployment

```bash
# Deploy to AKS with testing configuration
helm install legal-mcp .devops/helm/legal-mcp \
  -f .devops/helm/values/aks-testing.yaml \
  --namespace legal-mcp \
  --create-namespace
```

### Upgrade

```bash
helm upgrade legal-mcp .devops/helm/legal-mcp -f .devops/helm/values/aks-testing.yaml
```

### Uninstall

```bash
helm uninstall legal-mcp
```

## Configuration

### Key Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Container image repository | `ghcr.io/l4b4r4b4b4/legal-mcp` |
| `image.tag` | Image tag (defaults to chart appVersion) | `""` |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `legalMcp.transport` | Transport mode: `stdio` or `sse` | `sse` |
| `legalMcp.port` | Port for SSE transport | `8000` |
| `legalMcp.logLevel` | Log level: DEBUG, INFO, WARNING, ERROR | `INFO` |
| `legalMcp.debug` | Enable debug mode | `false` |

### Service Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `service.type` | Kubernetes service type | `ClusterIP` |
| `service.port` | Service port | `8000` |
| `service.targetPort` | Container port | `8000` |

### Ingress Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ingress.enabled` | Enable ingress | `false` |
| `ingress.className` | Ingress class name | `""` |
| `ingress.hosts` | Ingress hosts configuration | See values.yaml |
| `ingress.tls` | TLS configuration | `[]` |

### Persistence

| Parameter | Description | Default |
|-----------|-------------|---------|
| `persistence.enabled` | Enable persistent storage | `false` |
| `persistence.storageClassName` | Storage class | `""` |
| `persistence.size` | Storage size | `10Gi` |
| `persistence.accessModes` | Access modes | `[ReadWriteOnce]` |

### Resource Management

| Parameter | Description | Default |
|-----------|-------------|---------|
| `resources.limits.cpu` | CPU limit | `2000m` |
| `resources.limits.memory` | Memory limit | `4Gi` |
| `resources.requests.cpu` | CPU request | `500m` |
| `resources.requests.memory` | Memory request | `1Gi` |

### Autoscaling

| Parameter | Description | Default |
|-----------|-------------|---------|
| `autoscaling.enabled` | Enable HPA | `false` |
| `autoscaling.minReplicas` | Minimum replicas | `1` |
| `autoscaling.maxReplicas` | Maximum replicas | `5` |
| `autoscaling.targetCPUUtilizationPercentage` | Target CPU utilization | `80` |

## Values Files

Pre-configured values files are available in `.devops/helm/values/`:

| File | Description |
|------|-------------|
| `example.yaml` | Minimal configuration for quick start |
| `aks-testing.yaml` | AKS testing environment with debug enabled |

## Azure AKS Deployment

### Prerequisites

1. Azure CLI authenticated: `az login`
2. AKS credentials: `az aks get-credentials --resource-group <rg> --name <cluster>`

### Deploy with Ingress

```bash
# Install NGINX Ingress Controller (if not present)
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx

# Deploy Legal-MCP
helm install legal-mcp .devops/helm/legal-mcp \
  -f .devops/helm/values/aks-testing.yaml \
  --set ingress.hosts[0].host=legal-mcp.yourdomain.com
```

### Using Azure Container Registry

```bash
# Create pull secret
kubectl create secret docker-registry acr-pull-secret \
  --docker-server=<acr-name>.azurecr.io \
  --docker-username=<client-id> \
  --docker-password=<client-secret>

# Deploy with ACR image
helm install legal-mcp .devops/helm/legal-mcp \
  --set image.repository=<acr-name>.azurecr.io/legal-mcp \
  --set imagePullSecrets[0].name=acr-pull-secret
```

## Troubleshooting

### View Logs

```bash
kubectl logs -f deployment/legal-mcp
```

### Check Pod Status

```bash
kubectl get pods -l app.kubernetes.io/name=legal-mcp
kubectl describe pod -l app.kubernetes.io/name=legal-mcp
```

### Test Connectivity

```bash
# Port forward for local testing
kubectl port-forward svc/legal-mcp 8000:8000

# Test health endpoint
curl http://localhost:8000/health
```

### Common Issues

**Pod not starting:**
- Check resources: `kubectl describe pod <pod-name>`
- Check image pull: `kubectl get events --sort-by='.lastTimestamp'`

**Health check failing:**
- Increase `probes.liveness.initialDelaySeconds`
- Check application logs for startup errors

**Persistence issues:**
- Verify storage class: `kubectl get storageclass`
- Check PVC status: `kubectl get pvc`

## Development

### Template Rendering

```bash
# Render templates without installing
helm template legal-mcp .devops/helm/legal-mcp -f .devops/helm/values/example.yaml

# Debug with verbose output
helm install legal-mcp .devops/helm/legal-mcp --debug --dry-run
```

### Linting

```bash
helm lint .devops/helm/legal-mcp
```

## License

MIT License - see [LICENSE](../../../LICENSE) for details.
# Local Embeddings Model Lifecycle

`devx` supports optional local ONNX embeddings for semantic context selection and caching. This document covers the complete lifecycle: installation, download, caching, upgrade, offline use, and removal.

## Overview

Local embeddings run entirely on your machine — no API calls, no data leaves your workspace. They're used for:

- **Semantic context selection**: Ranking files by relevance to the task
- **Semantic caching**: Caching agent responses by semantic similarity

## Installation

Enable the `embeddings` extra when installing:

```bash
pip install -e ".[embeddings]"
# or
pip install "devx-coding-agent[embeddings]"
```

This installs:
- `numpy` — numerical operations
- `onnxruntime` — ONNX model inference
- `transformers` — model loading/tokenization
- `sentence-transformers` — embedding model wrappers

## Configuration

Enable embeddings via environment variables or config:

```env
# ~/.devx/config.env or workspace .env
DEVX_EMBEDDINGS_ENABLED=true
DEVX_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
DEVX_EMBEDDING_CACHE_DIR=~/.cache/devx/embeddings
DEVX_EMBEDDINGS_OFFLINE=false
```

Or in `.devx/config.json` (project-scoped):

```json
{
  "context_compaction": {
    "enabled": true
  }
}
```

**Note:** Embeddings are automatically used when `DEVX_EMBEDDINGS_ENABLED=true` and the dependencies are installed.

## Model Download & Caching

### First Run

On first use with embeddings enabled:
1. The model is downloaded from Hugging Face Hub
2. Converted to ONNX format (cached)
3. Stored in `DEVX_EMBEDDING_CACHE_DIR`

```bash
# First run - downloads ~90MB for all-MiniLM-L6-v2
devx ask "explain @src/main.py"
```

### Cache Location

Default: `~/.cache/devx/embeddings/`

Structure:
```
~/.cache/devx/embeddings/
├── sentence-transformers_all-MiniLM-L6-v2/
│   ├── config.json
│   ├── tokenizer.json
│   ├── tokenizer_config.json
│   ├── onnx/
│   │   └── model.onnx
│   └── modules.json
```

### Offline Mode

For air-gapped environments or to prevent accidental downloads:

```env
DEVX_EMBEDDINGS_OFFLINE=true
```

When `OFFLINE=true`:
- Models **must** be pre-downloaded
- Missing models raise an error instead of downloading
- Use `DEVX_EMBEDDING_CACHE_DIR` to point to a pre-populated cache

## Pre-populating Cache (Air-gapped Deployment)

### Option 1: Download on Connected Machine

```bash
# On machine with internet
DEVX_EMBEDDING_CACHE_DIR=/tmp/embeddings-cache python -c "
from devx.token_optim.embeddings import LocalEmbedder
embedder = LocalEmbedder('sentence-transformers/all-MiniLM-L6-v2', '/tmp/embeddings-cache', offline=False)
# Trigger download
embedder.embed(['test'])
"

# Copy to target machine
scp -r /tmp/embeddings-cache user@target:~/.cache/devx/embeddings
```

### Option 2: Use huggingface-cli

```bash
# Download model files
huggingface-cli download sentence-transformers/all-MiniLM-L6-v2 \
  --local-dir ~/.cache/devx/embeddings/sentence-transformers_all-MiniLM-L6-v2

# Convert to ONNX (requires optimum)
pip install optimum[onnxruntime]
python -m optimum.exporters.onnx \
  --model ~/.cache/devx/embeddings/sentence-transformers_all-MiniLM-L6-v2 \
  --output ~/.cache/devx/embeddings/sentence-transformers_all-MiniLM-L6-v2/onnx
```

## Model Upgrade

To upgrade to a newer model version:

1. **Change model identifier** in config:
   ```env
   DEVX_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L12-v2
   ```

2. **Clear old cache** (optional, saves disk space):
   ```bash
   rm -rf ~/.cache/devx/embeddings/sentence-transformers_all-MiniLM-L6-v2
   ```

3. **Run agent** — new model downloads automatically

### Recommended Models

| Model | Dimensions | Size | Speed | Quality |
|-------|------------|------|-------|---------|
| `all-MiniLM-L6-v2` | 384 | ~90MB | Fast | Good |
| `all-MiniLM-L12-v2` | 384 | ~120MB | Medium | Better |
| `all-mpnet-base-v2` | 768 | ~420MB | Slower | Best |
| `bge-small-en-v1.5` | 384 | ~130MB | Fast | Very Good |
| `bge-base-en-v1.5` | 768 | ~440MB | Medium | Excellent |

## Cache Invalidation

The cache is automatically invalidated when:
- Model identifier changes (`DEVX_EMBEDDING_MODEL`)
- Embedding model files are modified
- `DEVX_EMBEDDINGS_OFFLINE` toggles

Manual invalidation:
```bash
# Remove specific model
rm -rf ~/.cache/devx/embeddings/sentence-transformers_all-MiniLM-L6-v2

# Remove all embeddings
rm -rf ~/.cache/devx/embeddings
```

## Removing Embeddings

To completely disable and remove:

1. **Disable in config**:
   ```env
   DEVX_EMBEDDINGS_ENABLED=false
   ```

2. **Uninstall dependencies** (optional):
   ```bash
   pip uninstall numpy onnxruntime transformers sentence-transformers
   ```

3. **Remove cache** (optional):
   ```bash
   rm -rf ~/.cache/devx/embeddings
   ```

## Troubleshooting

### "Model not found" Error

```bash
# Ensure model identifier is correct
DEVX_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Check internet connectivity (if not offline)
curl -I https://huggingface.co
```

### ONNX Runtime Errors

```bash
# Reinstall onnxruntime
pip uninstall onnxruntime && pip install onnxruntime

# For GPU (CUDA):
pip install onnxruntime-gpu
```

### Import Errors

```bash
# Ensure all dependencies installed
pip install "devx-coding-agent[embeddings]"

# Check Python version (requires 3.11+)
python --version
```

### Performance Issues

- Use smaller models (`all-MiniLM-L6-v2`) for speed
- Reduce `DEVX_MAX_CONTEXT_FILES` to limit embeddings computed per run
- Ensure SSD storage for cache directory

## Advanced: Custom Models

Any sentence-transformers compatible model works:

```env
DEVX_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

For private/local models:
```env
DEVX_EMBEDDING_MODEL=/path/to/local/model
DEVX_EMBEDDINGS_OFFLINE=true
```

The model directory must contain:
- `config.json`
- `tokenizer.json` / `tokenizer_config.json`
- `pytorch_model.bin` or `model.safetensors` (auto-converted to ONNX)

## Cache Namespacing

Semantic cache is isolated by:
- Provider (`openai`, `anthropic`, etc.)
- Model name (`gpt-4o`, `claude-3-5-sonnet`)
- Embedder model (`all-MiniLM-L6-v2`)

This prevents cache pollution across different model/embedder combinations.
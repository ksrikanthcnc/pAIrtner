#!/bin/bash
set -e

echo ""
echo "  ============================================"
echo "  pAIrtner Couples Bot"
echo "  ============================================"
echo ""
echo "  Dashboard:  http://localhost:8765"
echo "  REST API:   http://localhost:8080"
echo "  Health:     http://localhost:8080/healthz"
echo ""
echo "  Press Ctrl+C to stop"
echo "  ============================================"
echo ""

exec "$@"

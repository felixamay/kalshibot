#!/usr/bin/env bash
# Deploy CourtEdge frontend to Firebase Hosting.
# Usage:
#   export FIREBASE_TOKEN='<token from firebase login:ci>'
#   export FIREBASE_PROJECT_ID='courledge-live'   # optional
#   export NEXT_PUBLIC_API_URL='https://your-api.example.com'
#   export NEXT_PUBLIC_WS_URL='wss://your-api.example.com/ws'
#   ./scripts/firebase-deploy.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${FIREBASE_TOKEN:-}" ]]; then
  echo "Missing FIREBASE_TOKEN. Generate one with: npx firebase login:ci"
  echo "Then: export FIREBASE_TOKEN='...'"
  exit 1
fi

PROJECT_ID="${FIREBASE_PROJECT_ID:-courledge-live}"
echo "Using Firebase project: $PROJECT_ID"

# Ensure project exists (create if missing)
if ! npx firebase projects:list --token "$FIREBASE_TOKEN" | grep -q "$PROJECT_ID"; then
  echo "Creating Firebase project $PROJECT_ID ..."
  npx firebase projects:create "$PROJECT_ID" --display-name "CourtEdge Live" --token "$FIREBASE_TOKEN" || true
fi

npx firebase use "$PROJECT_ID" --token "$FIREBASE_TOKEN"

# Build static frontend
export FIREBASE_HOSTING=1
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:8000}"
export NEXT_PUBLIC_WS_URL="${NEXT_PUBLIC_WS_URL:-ws://localhost:8000/ws}"
echo "Building frontend with API=$NEXT_PUBLIC_API_URL"
(cd frontend && npm run build)

# Init hosting target if needed
npx firebase target:apply hosting app "$PROJECT_ID" --token "$FIREBASE_TOKEN" 2>/dev/null || true

npx firebase deploy --only hosting --project "$PROJECT_ID" --token "$FIREBASE_TOKEN" --non-interactive

echo ""
echo "Deployed. Default URL:"
echo "  https://${PROJECT_ID}.web.app"
echo "  https://${PROJECT_ID}.firebaseapp.com"
echo ""
echo "Add a custom domain in Firebase Console → Hosting → Add custom domain"

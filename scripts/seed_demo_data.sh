#!/usr/bin/env bash
# Seed a demo truth graph for the dashboard.
#
# Builds a small product-knowledge story across all three default layers, with
# cross-layer derivation edges, multi-version facts, and one retraction so the
# dashboard has variety to render.
#
# Usage:
#   rm -f af.db            # optional: start clean
#   ./scripts/seed_demo_data.sh
#   uv run af-dashboard    # then open http://localhost:7373

set -euo pipefail

cd "$(dirname "$0")/.."

AF="uv run af"

UUID_RE='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

# Run `af fact create`, echo its output, and capture both fact_id and fv_id.
# Output format: "Created fact <fact-uuid> (fv <fv-uuid>, v1, weight=...)"
# Rich strips markup when stdout is not a TTY, so the regex sees plain text.
af_create() {
    local fact_var=$1
    local fv_var=$2
    shift 2
    local output
    output=$("$@")
    printf '%s\n' "$output"
    local uuids
    uuids=$(printf '%s\n' "$output" | grep -oE "$UUID_RE")
    printf -v "$fact_var" '%s' "$(printf '%s\n' "$uuids" | sed -n '1p')"
    printf -v "$fv_var"   '%s' "$(printf '%s\n' "$uuids" | sed -n '2p')"
}

# Same idea for `af fact update`. Output:
#   "Appended version v<n> (fv <fv-uuid>, weight=...) to fact <fact-uuid>"
af_update() {
    local fv_var=$1
    shift
    local output
    output=$("$@")
    printf '%s\n' "$output"
    local uuids
    uuids=$(printf '%s\n' "$output" | grep -oE "$UUID_RE")
    printf -v "$fv_var" '%s' "$(printf '%s\n' "$uuids" | sed -n '1p')"
}

echo "==> 1. Ensure schema + default layers"
$AF init

echo
echo "==> 2. Canonical layer — immutable product facts"

af_create PRICING_FACT  PRICING_FV  $AF fact create \
    --layer canonical \
    --content '{"rule": "pricing_tiers", "tiers": {"free": 0, "pro": 10, "enterprise": 100}, "currency": "USD"}' \
    --note "Product pricing as of GA launch"

af_create EMAIL_FACT    EMAIL_FV    $AF fact create \
    --layer canonical \
    --content '{"rule": "checkout_requires_verified_email"}' \
    --weight 95 \
    --note "Compliance requirement"

af_create CURRENCY_FACT CURRENCY_FV $AF fact create \
    --layer canonical \
    --content '{"rule": "single_currency", "currency": "USD"}' \
    --note "No FX support in v1"

echo
echo "==> 3. Episodic layer — user interaction history (cites canonical facts)"

af_create SIGNUP_FACT   SIGNUP_FV   $AF fact create \
    --layer episodic \
    --content '{"user": "alice@example.com", "event": "signup", "tier": "free", "at": "2026-05-25T10:14:00Z"}' \
    --edges-to "$PRICING_FV" \
    --note "Onboarded via marketing site"

af_create UPGRADE_FACT  UPGRADE_FV  $AF fact create \
    --layer episodic \
    --content '{"user": "alice@example.com", "event": "upgrade", "from": "free", "to": "pro", "at": "2026-05-27T16:02:00Z"}' \
    --edges-to "$PRICING_FV" \
    --edges-to "$EMAIL_FV" \
    --note "Used in-app upgrade flow"

echo
echo "==> 4. Append a v2 to the signup fact (timestamp correction)"
af_update SIGNUP_V2_FV $AF fact update \
    --fact-id "$SIGNUP_FACT" \
    --content '{"user": "alice@example.com", "event": "signup", "tier": "free", "at": "2026-05-25T10:14:32Z", "source_of_truth": "auth_log"}' \
    --note "Corrected timestamp from auth log (was approximate)"

echo
echo "==> 5. Living layer — LLM-derived hypotheses (cites episodic + canonical)"

af_create HYPOTHESIS_FACT HYPOTHESIS_FV $AF fact create \
    --layer living \
    --content '{"about": "alice@example.com", "hypothesis": "values_automation", "confidence": 0.62}' \
    --edges-to "$UPGRADE_FV" \
    --note "Inferred from rapid free->pro upgrade pattern"

af_create UPSELL_FACT     UPSELL_FV     $AF fact create \
    --layer living \
    --content '{"about": "alice@example.com", "action": "recommend", "target_tier": "enterprise"}' \
    --weight 30 \
    --edges-to "$HYPOTHESIS_FV" \
    --edges-to "$PRICING_FV" \
    --note "Auto-generated upsell candidate"

echo
echo "==> 6. Append a refined v2 to the hypothesis (raised confidence)"
af_update HYPOTHESIS_V2_FV $AF fact update \
    --fact-id "$HYPOTHESIS_FACT" \
    --content '{"about": "alice@example.com", "hypothesis": "values_automation", "confidence": 0.78}' \
    --edges-to "$SIGNUP_V2_FV" \
    --note "Confidence raised after observing API key creation"

echo
echo "==> 7. Retract the upsell recommendation (false positive)"
$AF fact retract \
    --fact-id "$UPSELL_FACT" \
    --note "Retracted: alice opted out of marketing emails after upgrade"

echo
echo "==> 8. Final state"
$AF layer list
$AF fact list --all-versions

echo
echo "Done. Launch the dashboard with:  uv run af-dashboard"

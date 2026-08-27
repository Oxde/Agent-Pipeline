#!/bin/sh
# Everything the engine knows arrives as environment variables, so a hook that
# only forwards $AGENT_PIPELINE_MESSAGE already sends something readable.
#
#   export TG_TOKEN=123456:AA...    TG_CHAT=987654321
#
# Exiting non-zero here is logged and ignored — a notification must never be
# able to stop a run.
set -eu
: "${TG_TOKEN:?set TG_TOKEN}" "${TG_CHAT:?set TG_CHAT}"

curl -sS -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT}" \
  --data-urlencode "text=${AGENT_PIPELINE_MESSAGE}" > /dev/null

#!/usr/bin/env bash
set -euo pipefail

if [[ -f ./"harbor.env" ]]; then
  # shellcheck disable=SC1091
  source ./harbor.env
fi

: "${HARBOR_REGISTRY:?HARBOR_REGISTRY is required}"
: "${HARBOR_PROJECT:?HARBOR_PROJECT is required}"
: "${IMAGE_NAME:=traffic-app}"
: "${IMAGE_TAG:=latest}"
: "${HARBOR_USERNAME:?HARBOR_USERNAME is required}"
: "${HARBOR_PASSWORD:?HARBOR_PASSWORD is required}"

IMAGE_REF="${HARBOR_REGISTRY}/${HARBOR_PROJECT}/${IMAGE_NAME}:${IMAGE_TAG}"

echo "${HARBOR_PASSWORD}" | docker login "${HARBOR_REGISTRY}" -u "${HARBOR_USERNAME}" --password-stdin
docker build -t "${IMAGE_REF}" .
docker push "${IMAGE_REF}"

echo "Pushed ${IMAGE_REF}"
